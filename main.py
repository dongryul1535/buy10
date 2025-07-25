#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py

KIS OpenAPI 인증 + 외국인 순매수 상위 10종목 조회
+ FinanceDataReader로 6개월치 가격 데이터 조회
+ NH MTS 스타일 Composite MACD+Stochastic 차트 작성
+ Golden/Dead Cross 감지 시 Telegram 알림

환경변수:
  KIS_APP_KEY, KIS_APP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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

# ──────────────────────────────────────────────────────────────────────────────
# 1) KIS OpenAPI 인증
# ──────────────────────────────────────────────────────────────────────────────
API_KEY    = os.getenv("KIS_APP_KEY")  # 발급받은 앱키
API_SECRET = os.getenv("KIS_APP_SECRET")  # 발급받은 앱시크릿
TOKEN_URL  = "https://openapi.koreainvestment.com:9443/oauth2/token"
_access_token = None

def auth():
    """KIS OpenAPI 토큰을 발급받아 _access_token에 저장합니다."""
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
        raise RuntimeError(f"토큰 발급 응답 오류: {resp.text}")
    _access_token = token

# ──────────────────────────────────────────────────────────────────────────────
# 2) 외국인 매매종목가집계 조회
# ──────────────────────────────────────────────────────────────────────────────
API_URL = (
    "https://openapi.koreainvestment.com:9443"
    "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
)
TR_ID   = "FHKSTA01400"
PARAMS = {
    "fid_cond_mrkt_div_code": "V",
    "fid_cond_scr_div_code": "16449",
    "fid_input_iscd": "0000",
    "fid_div_cls_code": "0",
    "fid_rank_sort_cls_code": "0",
    "fid_etc_cls_code": "0"
}

def fetch_top10_foreign() -> pd.DataFrame:
    """외국인 순매수 거래대금 상위 10종목 반환"""
    if not _access_token:
        raise RuntimeError("토큰이 없습니다. auth()를 먼저 호출하세요.")
    headers = {
        "Content-Type":  "application/json; charset=UTF-8",
        "Authorization": f"Bearer {_access_token}",
        "appkey":        API_KEY,
        "appsecret":     API_SECRET,
        "tr_id":         TR_ID,
        "custtype":      "P"
    }
    # UAPI 호출, 최대 3회 재시도
    for attempt in range(1, 4):
        resp = requests.post(API_URL, headers=headers, json=PARAMS, timeout=10)
        if resp.status_code == 200:
            break
        logging.warning(f"UAPI 요청 {attempt}회차 실패: {resp.status_code} {resp.text}")
        time.sleep(1)
    else:
        logging.error("UAPI 모든 시도 실패, 빈 DataFrame 반환")
        return pd.DataFrame()

    data = resp.json()
    items = data.get("output", {}).get("foreignInstitutionTotals", [])
    df = pd.DataFrame(items)
    if df.empty:
        logging.warning("조회된 데이터가 없습니다.")
        return df
    df = df.rename(columns={
        "mksc_shrn_iscd":    "종목코드",
        "hts_kor_isnm":      "종목명",
        "frgn_ntby_tr_pbmn": "외국인 순매수 거래대금"
    })
    df["외국인 순매수 거래대금"] = pd.to_numeric(df["외국인 순매수 거래대금"], errors="coerce")
    return df.sort_values("외국인 순매수 거래대금", ascending=False).head(10)

# ──────────────────────────────────────────────────────────────────────────────
# 3) Telegram 알림 함수
# ──────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
    raise RuntimeError("환경변수 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID을 설정해주세요.")
SEND_MSG_URL   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
SEND_PHOTO_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

def send_message(text: str):
    resp = requests.post(SEND_MSG_URL, data={"chat_id": TELEGRAM_CHAT, "text": text})
    resp.raise_for_status()

def send_photo(image_bytes: bytes, caption: str = ""):
    files = {"photo": ("chart.png", image_bytes)}
    data  = {"chat_id": TELEGRAM_CHAT, "caption": caption}
    resp = requests.post(SEND_PHOTO_URL, files=files, data=data)
    resp.raise_for_status()

# ──────────────────────────────────────────────────────────────────────────────
# 4) 지표 계산 및 시그널 탐지
# ──────────────────────────────────────────────────────────────────────────────

def analyze_symbol(code: str, name: str):
    """6개월 가격으로 Composite MACD+Stochastic 교차 시그널 알림"""
    end_date = datetime.today().date()
    start_date = end_date - relativedelta(months=6)
    df = fdr.DataReader(code, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    if df.empty:
        logging.warning(f"{code}({name}) 데이터 조회 실패")
        return
    # MACD
    ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    # Stochastic
    low14 = df["Low"].rolling(14).min()
    high14 = df["High"].rolling(14).max()
    stoch_k = (df["Close"] - low14) / (high14 - low14) * 100
    stoch_d = stoch_k.rolling(3).mean()
    # Composite
    comp_k = macd_line + stoch_k
    comp_d = signal_line + stoch_d
    # 시그널 탐지
    if len(comp_k) < 2:
        return
    prev_k, prev_d = comp_k.iloc[-2], comp_d.iloc[-2]
    curr_k, curr_d = comp_k.iloc[-1], comp_d.iloc[-1]
    signal = None
    if prev_k < prev_d and curr_k > curr_d:
        signal = "BUY"
    elif prev_k > prev_d and curr_k < curr_d:
        signal = "SELL"
    if signal:
        plt.figure(figsize=(10, 6))
        plt.plot(df.index, comp_k, label="Composite K")
        plt.plot(df.index, comp_d, label="Composite D")
        plt.title(f"{name}({code}) Composite MACD+Stoch")
        plt.legend()
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        caption = f"{name}({code}) - {signal} 신호 발생"
        send_photo(buf.getvalue(), caption)
        send_message(f"{name}({code}): {signal} 신호를 전송했습니다.")

# ──────────────────────────────────────────────────────────────────────────────
# 5) 메인 실행
# ──────────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("1) KIS API 인증 시작")
    auth()
    logging.info("2) KIS API 인증 완료")
    top10 = fetch_top10_foreign()
    if top10.empty:
        logging.error("상위 종목 조회 실패, 프로그램 종료")
        return
    print("\n=== 외국인 순매수 거래대금 상위 10종목 ===\n")
    print(top10[["종목코드", "종목명", "외국인 순매수 거래대금"]])
    for _, row in top10.iterrows():
        analyze_symbol(row["종목코드"], row["종목명"])

if __name__ == "__main__":
    main()
