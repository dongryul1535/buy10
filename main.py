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
TR_ID   = "FHKSTA01400"
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
        print(resp.text)  # ← 디버깅을 위해 응답 전문 출력
        if resp.status_code == 200:
            break
        logging.warning(f"UAPI GET {attempt}회차 실패: {resp.status_code} {resp.text}")
        time.sleep(1)
    else:
        logging.error("UAPI 모든 시도 실패")
        return pd.DataFrame()

    payload = resp.json().get("output", {})
    items = payload.get("foreignInstitutionTotals") or payload.get("foreignInstitutionTotalList") or []
    if not items:
        logging.warning(f"조회 결과 없음: {payload}")
        return pd.DataFrame()

    df = pd.DataFrame(items)
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
def analyze_symbol(code: str, name: str):
    now = datetime.now(KST)
    start = (now - relativedelta(months=6)).date()
    end   = now.date()
    df = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df.empty:
        logging.warning(f"{code}({name}) 데이터 조회 실패: {start}~{end}")
        return
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
        plt.figure(figsize=(10,6))
        plt.plot(df.index, comp_k, label="Composite K")
        plt.plot(df.index, comp_d, label="Composite D")
        plt.title(f"{name}({code}) {signal} ({start}~{end})")
        plt.legend()
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        send_photo(buf.getvalue(), f"{name}({code}) - {signal}")
        send_message(f"{name}({code}): {signal} 신호 발생 ({start}~{end})")

# 5) 메인 실행
class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, KST).timetuple()

def main():
    # KST 타임존 포매터 적용
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
        analyze_symbol(row["종목코드"], row["종목명"])

if __name__ == "__main__":
    main()
