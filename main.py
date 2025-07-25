import os
import time
import logging
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import io
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 한글 폰트 적용 (있으면 NanumGothic, 없으면 sans-serif)
FONT_PATH = os.getenv("FONT_PATH", "fonts/NanumGothic.ttf")
font_path = FONT_PATH
from matplotlib import font_manager, rc
import warnings

if os.path.exists(font_path):
    font_manager.fontManager.addfont(font_path)
    plt.rc('font', family='NanumGothic')
    fontprop = font_manager.FontProperties(fname=font_path)
else:
    warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib.font_manager")
    plt.rc('font', family='sans-serif')
    fontprop = None

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

# --- NH MACD+Stoch (main(4).py 방식) ---
def add_composites(df: pd.DataFrame,
                   fast=12, slow=26,
                   k_window=14, k_smooth=3,
                   d_smooth=3, use_ema=True, clip=True) -> pd.DataFrame:
    close, high, low = df['Close'], df['High'], df['Low']

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_raw = ema_fast - ema_slow

    macd_min = macd_raw.rolling(k_window, min_periods=1).min()
    macd_max = macd_raw.rolling(k_window, min_periods=1).max()
    macd_norm = (macd_raw - macd_min) / (macd_max - macd_min).replace(0, np.nan) * 100
    macd_norm = macd_norm.fillna(50)
    if k_smooth > 1:
        macd_norm = macd_norm.ewm(span=k_smooth, adjust=False).mean() if use_ema \
            else macd_norm.rolling(k_smooth, min_periods=1).mean()

    ll = low.rolling(k_window, min_periods=1).min()
    hh = high.rolling(k_window, min_periods=1).max()
    k_raw = (close - ll) / (hh - ll).replace(0, np.nan) * 100
    k_raw = k_raw.fillna(50)
    slow_k = (k_raw.ewm(span=k_smooth, adjust=False).mean() if (k_smooth > 1 and use_ema)
              else k_raw.rolling(k_smooth, min_periods=1).mean() if k_smooth > 1 else k_raw)

    comp_k = (macd_norm + slow_k) / 2.0
    comp_d = comp_k.rolling(d_smooth, min_periods=1).mean() if d_smooth > 1 else comp_k

    if clip:
        comp_k = comp_k.clip(0, 100)
        comp_d = comp_d.clip(0, 100)

    df['CompK'] = comp_k
    df['CompD'] = comp_d
    df['Diff']  = comp_k - comp_d
    return df

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

    df["MA20"] = df["Close"].rolling(20).mean()
    today_close = df["Close"].iloc[-1]
    yesterday_close = df["Close"].iloc[-2] if len(df) > 1 else today_close
    change = today_close - yesterday_close
    change_rate = change / yesterday_close * 100 if yesterday_close else 0

    df = add_composites(df)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10,8), sharex=True, gridspec_kw={'height_ratios':[2,1]})
    ax1.plot(df.index, df["Close"], label="종가")
    ax1.plot(df.index, df["MA20"], label="MA20", linestyle="--")
    ax1.set_title(f"{code}.KS ({name})", fontproperties=fontprop)
    ax1.legend(loc="best", prop=fontprop)
    ax1.grid(True)

    ax2.plot(df.index, df["CompK"], color="red", label="MACD+Slow%K")
    ax2.plot(df.index, df["CompD"], color="purple", label="MACD+Slow%D")
    ax2.axhline(20, color="gray", linestyle="--", linewidth=0.5)
    ax2.axhline(80, color="gray", linestyle="--", linewidth=0.5)
    ax2.set_ylim(0, 100)
    ax2.set_title("MACD+Stochastic (NH Style)", fontproperties=fontprop)
    ax2.legend(loc="best", prop=fontprop)
    ax2.grid(True)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    signal = None
    if len(df) >= 2:
        prev_diff, curr_diff = df['Diff'].iloc[-2], df['Diff'].iloc[-1]
        prev_k = df['CompK'].iloc[-2]
        if prev_diff <= 0 < curr_diff:
            signal = "BUY" if prev_k < 20 else "BUY_W"
        elif prev_diff >= 0 > curr_diff:
            signal = "SELL" if prev_k > 80 else "SELL_W"

    trading_value_str = f"{trading_value:,}" if trading_value is not None else "-"
    msg = (
        f"{name} ({code})\n"
        f"외국인 순매수 거래대금: {trading_value_str}백만원\n"
        f"현재가: {today_close:,.0f}원 ({change:+,.0f} / {change_rate:+.2f}%)\n"
    )
    if signal:
        msg += f"시그널: {signal}"

    send_photo(buf.getvalue(), caption=msg)

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
