#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py

KIS OpenAPI 인증 + 외국인 순매수 상위 10종목 조회
+ FinanceDataReader로 6개월치 가격 데이터 조회
+ NH MTS 스타일 Composite MACD+Stochastic 차트 작성 (가격+MA20, MACD+SlowK/D)
+ Golden/Dead Cross 감지 시 Telegram 알림 (거래대금, 등락률, 전일비, 한글 폰트)
+ 모든 날짜 연산을 한국 표준시(Asia/Seoul) 기준으로 처리

환경변수:
  KIS_APP_KEY, KIS_APP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  FONT_PATH: fonts/NanumGothic.ttf (선택, 한글 폰트)

필수 패키지:
  requests, pandas, FinanceDataReader, matplotlib, python-dateutil
선택 패키지 (타임존 처리):
  pytz (Python <3.9 환경에서 필요한 경우)
"""

import os
import time
import logging
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 한글 폰트 적용
font_path = os.getenv("FONT_PATH", "fonts/NanumGothic.ttf")
from matplotlib import font_manager, rc
if os.path.exists(font_path):
    fontprop = font_manager.FontProperties(fname=font_path)
    plt.rc('font', family=fontprop.get_name())
else:
    fontprop = None  # fallback

# 타임존 처리
try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo('Asia/Seoul')
except ImportError:
    import pytz
    KST = pytz.timezone('Asia/Seoul')

# 1) KIS 인증
API_KEY    = os.getenv("KIS_APP_KEY")
API_SECRET = os.getenv("KIS_APP_SECRET")
TOKEN_URL  = "https://openapi.koreainvestment.com:9443/oauth2/token"
_access_token = None

def auth():
    global _access_token
    if not API_KEY or not API_SECRET:
        raise RuntimeError("환경변수 KIS_APP_KEY/KIS_APP_SECRET을 설정해주세요.")
    data = {"grant_type": "client_credentials", "appkey": API_KEY, "appsecret": API_SECRET}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(TOKEN_URL, data=data, headers=headers)
    if resp.status_code != 200:
        logging.error(f"토큰 발급 실패: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 오류: {resp.text}")
    _access_token = token

# 2) 외국인 매매종목가집계 조회 (GET 방식)
API_URL = (
    "https://openapi.koreainvestment.com:9443"
    "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
)
TR_ID   = "FHPTJ04400000"
PARAMS = {
    "fid_cond_mrkt_div_code":    "V",
    "fid_cond_scr_div_code":     "16449",
    "fid_input_iscd":            "0000",
    "fid_div_cls_code":          "0",
    "fid_rank_sort_cls_code":    "0",
    "fid_etc_cls_code":          "0"
}

def fetch_top10_foreign() -> pd.DataFrame:
    """외국인 순매수 거래대금 상위 10종목 (KOSPI+KOSDAQ)"""
    if not _access_token:
        raise RuntimeError("auth()를 먼저 호출하세요.")
    headers = {
        "Authorization": f"Bearer {_access_token}",
        "appkey":        API_KEY,
        "appsecret":     API_SECRET,
        "tr_id":         TR_ID,
        "custtype":      "P"
    }
    for attempt in range(1, 4):
        resp = requests.get(API_URL, headers=headers, params=PARAMS, timeout=10)
        print(resp.text)  # 디버그용
        if resp.status_code == 200:
            break
        logging.warning(f"UAPI GET {attempt}회차 실패: {resp.status_code} {resp.text}")
        time.sleep(1)
    else:
        logging.error("UAPI 모든 시도 실패")
        return pd.DataFrame()

    payload = resp.json().get("output", [])
    if not payload or not isinstance(payload, list):
        logging.warning(f"조회 결과 없음: {payload}")
        return pd.DataFrame()
    df = pd.DataFrame(payload)
    col_map = {
        "mksc_shrn_iscd":    "종목코드",
        "hts_kor_isnm":      "종목명",
        "frgn_ntby_tr_pbmn": "외국인 순매수 거래대금"
    }
    existing = {k:v for k,v in col_map.items() if k in df.columns}
    df = df.rename(columns=existing)
    if "외국인 순매수 거래대금" in df.columns:
        df["외국인 순매수 거래대금"] = pd.to_numeric(df["외국인 순매수 거래대금"], errors="coerce")
    else:
        df["외국인 순매수 거래대금"] = pd.NA
    return df.sort_values("외국인 순매수 거래대금", ascending=False).head(10)

# 3) Telegram 알림 함수
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
    raise RuntimeError("환경변수 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID을 설정해주세요.")
SEND_MSG_URL   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
SEND_PHOTO_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

def send_message(text: str):
    resp = requests.post(SEND_MSG_URL, data={"chat_id": TELEGRAM_CHAT, "text": text})
    resp.raise_for_status()

def send_photo(img_bytes: bytes, caption: str = ""):
    files = {"photo": ("chart.png", img_bytes)}
    data  = {"chat_id": TELEGRAM_CHAT, "caption": caption}
    resp = requests.post(SEND_PHOTO_URL, files=files, data=data)
    resp.raise_for_status()

# 4) 분석 및 시그널 탐지
def analyze_symbol(code: str, name: str, trading_value: float = None):
    now = datetime.now(KST)
    start = (now - relativedelta(months=6)).date()
    end   = now.date()
    df = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df.empty:
        logging.warning(f"{code}({name}) 데이터 조회 실패: {start}~{end}")
        return

    # 가격/이동평균
    df["MA20"] = df["Close"].rolling(20).mean()
    today_close = df["Close"].iloc[-1]
    yesterday_close = df["Close"].iloc[-2] if len(df) > 1 else today_close
    change = today_close - yesterday_close
    change_rate = change / yesterday_close * 100 if yesterday_close else 0

    # MACD, Signal, Stochastic
    ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    low14 = df["Low"].rolling(14).min()
    high14 = df["High"].rolling(14).max()
    stoch_k = (df["Close"] - low14) / (high14 - low14) * 100
    stoch_d = stoch_k.rolling(3).mean()
    comp_k = macd_line + stoch_k
    comp_d = signal_line + stoch_d

    # 차트 그리기 (가격/MA20 + MACD+Stoch)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10,8), sharex=True, gridspec_kw={'height_ratios':[2,1]})
    ax1.plot(df.index, df["Close"], label="종가")
    ax1.plot(df.index, df["MA20"], label="MA20", linestyle="--")
    ax1.set_title(f"{code}.KS ({name})", fontproperties=fontprop)
    ax1.legend(loc="best", prop=fontprop)
    ax1.grid(True)

    ax2.plot(df.index, comp_k, label="MACD+Slow%K", color="red")
    ax2.plot(df.index, comp_d, label="MACD+Slow%D", color="purple")
    ax2.set_ylim(0, 100)
    ax2.set_title("MACD+Stochastic (NH Style)", fontproperties=fontprop)
    ax2.legend(loc="best", prop=fontprop)
    ax2.grid(True)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    # 시그널 감지
    signal = None
    if len(comp_k) >= 2:
        prev_k, prev_d = comp_k.iloc[-2], comp_d.iloc[-2]
        curr_k, curr_d = comp_k.iloc[-1], comp_d.iloc[-1]
        if prev_k < prev_d and curr_k > curr_d:
            signal = "BUY"
        elif prev_k > prev_d and curr_k < curr_d:
            signal = "SELL"

    # 텔레그램 메시지
    trading_value_str = f"{trading_value:,}" if trading_value is not None else "-"
    msg = (
        f"{name} ({code})\n"
        f"외국인 순매수 거래대금: {trading_value_str}백만원\n"
        f"현재가: {today_close:,.0f}원 ({change:+,.0f} / {change_rate:+.2f}%)\n"
    )
    if signal:
        msg += f"시그널: {signal}"

    send_photo(buf.getvalue(), caption=msg)
    send_message(msg)

# 5) 메인 실행
class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, KST).timetuple()

def main():
    handler = logging.StreamHandler()
    handler.setFormatter(KSTFormatter("%(asctime)s [%(levelname)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    logging.info("1) KIS API 인증 시작")
    auth()
    logging.info("2) KIS API 인증 완료")
    top10 = fetch_top10_foreign()
    if top10.empty:
        logging.error("상위 종목 조회 실패, 프로그램 종료")
        return
    print("\n=== 외국인 순매수 거래대금 상위 10종목 ===\n")
    print(top10[["종목코드","종목명","외국인 순매수 거래대금"]])
    for _, row in top10.iterrows():
        analyze_symbol(
            row["종목코드"],
            row["종목명"],
            row.get("외국인 순매수 거래대금")
        )

if __name__ == "__main__":
    main()
