import os
import time
import logging
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import io
import numpy as np
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ──────────────────────── 한글 폰트 ────────────────────────
FONT_PATH = os.getenv("FONT_PATH", "fonts/NanumGothic.ttf")
from matplotlib import font_manager
import warnings

if os.path.exists(FONT_PATH):
    font_manager.fontManager.addfont(FONT_PATH)
    plt.rc('font', family='NanumGothic')
    fontprop = font_manager.FontProperties(fname=FONT_PATH)
else:
    warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib.font_manager")
    plt.rc('font', family='sans-serif')
    fontprop = None

plt.rcParams['axes.unicode_minus'] = False

# ──────────────────────── 타임존 ────────────────────────
try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo('Asia/Seoul')
except ImportError:
    import pytz
    KST = pytz.timezone('Asia/Seoul')

# ════════════════════════ NH MTS 설정값 ════════════════════════
FAST_PERIOD = 12
SLOW_PERIOD = 26
K_WINDOW    = 14
K_SMOOTH    = 3
D_SMOOTH    = 3
OB_LINE     = 80
OS_LINE     = 20
DAILY_BARS  = 60       # ★ 일봉 60일
WEEKLY_BARS = 30       # 주봉 30주

COLOR_K = "#FF0000"
COLOR_D = "#9900FF"

# ════════════════════════ 1) KIS 인증 ════════════════════════
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

# ════════════════════════ 2) 외국인 매매 조회 ════════════════════════
API_URL = (
    "https://openapi.koreainvestment.com:9443"
    "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
)
TR_ID = "FHPTJ04400000"
PARAMS = {
    "fid_cond_mrkt_div_code":    "V",
    "fid_cond_scr_div_code":     "16449",
    "fid_input_iscd":            "0000",
    "fid_div_cls_code":          "0",
    "fid_rank_sort_cls_code":    "0",
    "fid_etc_cls_code":          "0"
}

def fetch_top10_foreign() -> pd.DataFrame:
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
    existing = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(columns=existing)
    if "외국인 순매수 거래대금" in df.columns:
        df["외국인 순매수 거래대금"] = pd.to_numeric(df["외국인 순매수 거래대금"], errors="coerce")
    else:
        df["외국인 순매수 거래대금"] = pd.NA
    return df.sort_values("외국인 순매수 거래대금", ascending=False).head(10)

# ════════════════════════ 3) MACD+Stochastic 지표 ════════════════════════
def add_composites(df: pd.DataFrame,
                   fast=FAST_PERIOD, slow=SLOW_PERIOD,
                   k_window=K_WINDOW, k_smooth=K_SMOOTH,
                   d_smooth=D_SMOOTH) -> pd.DataFrame:
    close = df['Close'].astype(float)
    high  = df['High'].astype(float)
    low   = df['Low'].astype(float)

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_raw = ema_fast - ema_slow

    macd_min = macd_raw.rolling(k_window, min_periods=1).min()
    macd_max = macd_raw.rolling(k_window, min_periods=1).max()
    denom = (macd_max - macd_min).replace(0, np.nan)
    macd_norm = ((macd_raw - macd_min) / denom * 100).fillna(50)
    if k_smooth > 1:
        macd_norm = macd_norm.ewm(span=k_smooth, adjust=False).mean()

    ll = low.rolling(k_window, min_periods=1).min()
    hh = high.rolling(k_window, min_periods=1).max()
    stoch_denom = (hh - ll).replace(0, np.nan)
    k_raw = ((close - ll) / stoch_denom * 100).fillna(50)
    slow_k = k_raw.ewm(span=k_smooth, adjust=False).mean() if k_smooth > 1 else k_raw

    comp_k = ((macd_norm + slow_k) / 2.0).clip(0, 100)
    comp_d = comp_k.rolling(d_smooth, min_periods=1).mean().clip(0, 100)

    df = df.copy()
    df['CompK'] = comp_k
    df['CompD'] = comp_d
    df['Diff']  = comp_k - comp_d
    return df


def detect_cross(df: pd.DataFrame) -> str | None:
    if len(df) < 2:
        return None
    prev_diff = df['Diff'].iloc[-2]
    curr_diff = df['Diff'].iloc[-1]
    prev_k    = df['CompK'].iloc[-2]
    if prev_diff <= 0 < curr_diff:
        return 'BUY' if prev_k < OS_LINE else 'BUY_W'
    if prev_diff >= 0 > curr_diff:
        return 'SELL' if prev_k > OB_LINE else 'SELL_W'
    return None

# ════════════════════════ 4) 주봉 리샘플링 ════════════════════════
def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    if 'Date' in tmp.columns:
        tmp['Date'] = pd.to_datetime(tmp['Date'])
        tmp = tmp.set_index('Date')
    weekly = tmp.resample('W-FRI').agg({
        'Open':   'first',
        'High':   'max',
        'Low':    'min',
        'Close':  'last',
        'Volume': 'sum',
    }).dropna(subset=['Close'])
    return weekly.reset_index()

# ════════════════════════ 5) 캔들스틱 그리기 ════════════════════════
def draw_candlestick(ax, df, width_ratio=0.6):
    dates = mdates.date2num(pd.to_datetime(df['Date']))
    if len(dates) >= 2:
        avg_gap = np.median(np.diff(dates))
    else:
        avg_gap = 1.0
    bar_width = avg_gap * width_ratio

    opens  = df['Open'].values.astype(float)
    highs  = df['High'].values.astype(float)
    lows   = df['Low'].values.astype(float)
    closes = df['Close'].values.astype(float)

    for i in range(len(dates)):
        d = dates[i]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]

        if c >= o:
            color = '#FF3232'
            body_bottom = o
            body_height = c - o
        else:
            color = '#3232FF'
            body_bottom = c
            body_height = o - c

        ax.plot([d, d], [l, h], color=color, linewidth=0.7, solid_capstyle='round')

        if body_height == 0:
            ax.plot([d - bar_width/2, d + bar_width/2], [o, o],
                    color=color, linewidth=1.0)
        else:
            rect = Rectangle(
                (d - bar_width / 2, body_bottom),
                bar_width, body_height,
                facecolor=color, edgecolor=color, linewidth=0.5
            )
            ax.add_patch(rect)

    ax.xaxis_date()

# ════════════════════════ 6) 패널 그리기 ════════════════════════
def _plot_panel(ax_candle, ax_ind, df, title, date_fmt):
    dates_dt  = pd.to_datetime(df['Date'])
    dates_num = mdates.date2num(dates_dt)
    close     = df['Close'].astype(float)

    draw_candlestick(ax_candle, df)

    ma5  = close.rolling(5,  min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ax_candle.plot(dates_num, ma5,  color='#FF8C00', linewidth=0.8, label='MA5')
    ax_candle.plot(dates_num, ma20, color='#1E90FF', linewidth=0.8,
                   linestyle='--', label='MA20')

    ax_candle.set_title(title, fontproperties=fontprop, fontsize=10, fontweight='bold')
    ax_candle.legend(prop=fontprop, fontsize=7, loc='upper left')
    ax_candle.tick_params(axis='both', labelsize=7)
    ax_candle.grid(True, alpha=0.25)
    ax_candle.set_xlim(dates_num[0] - 1, dates_num[-1] + 1)

    ax_ind.plot(dates_num, df['CompK'].values, color=COLOR_K, linewidth=1.0,
                label='MACD+Slow%K')
    ax_ind.plot(dates_num, df['CompD'].values, color=COLOR_D, linewidth=1.0,
                label='MACD+Slow%D')
    ax_ind.axhline(OS_LINE, color='gray', linestyle='--', linewidth=0.5)
    ax_ind.axhline(OB_LINE, color='gray', linestyle='--', linewidth=0.5)
    ax_ind.fill_between(dates_num, 0,       OS_LINE, alpha=0.06, color='blue')
    ax_ind.fill_between(dates_num, OB_LINE, 100,     alpha=0.06, color='red')
    ax_ind.set_ylim(0, 100)
    ax_ind.legend(prop=fontprop, fontsize=6, loc='upper left')
    ax_ind.tick_params(axis='both', labelsize=7)
    ax_ind.grid(True, alpha=0.25)
    ax_ind.set_xlim(dates_num[0] - 1, dates_num[-1] + 1)
    ax_ind.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt))

    last_k = df['CompK'].iloc[-1]
    last_d = df['CompD'].iloc[-1]
    ax_ind.annotate(f'{last_k:.1f}', xy=(dates_num[-1], last_k),
                    fontsize=7, color=COLOR_K, fontweight='bold',
                    xytext=(5, 3), textcoords='offset points')
    ax_ind.annotate(f'{last_d:.1f}', xy=(dates_num[-1], last_d),
                    fontsize=7, color=COLOR_D, fontweight='bold',
                    xytext=(5, -10), textcoords='offset points')

# ════════════════════════ 7) Telegram ════════════════════════
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

# ════════════════════════ 8) 분석 (1열 4행) ════════════════════════
def analyze_symbol(code: str, name: str, trading_value: float = None):
    now   = datetime.now(KST)
    start = (now - relativedelta(months=14)).date()
    end   = now.date()

    df_raw = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if df_raw.empty:
        logging.warning(f"{code}({name}) 데이터 조회 실패")
        return

    df_raw = df_raw.reset_index()
    if 'Date' not in df_raw.columns:
        df_raw = df_raw.rename(columns={df_raw.columns[0]: 'Date'})
    df_raw['Date'] = pd.to_datetime(df_raw['Date'])
    df_raw = df_raw.sort_values('Date').reset_index(drop=True)

    today_close     = float(df_raw['Close'].iloc[-1])
    yesterday_close = float(df_raw['Close'].iloc[-2]) if len(df_raw) > 1 else today_close
    change      = today_close - yesterday_close
    change_rate = change / yesterday_close * 100 if yesterday_close else 0

    # 일봉: 전체로 지표 계산 → 최근 60일만 표시
    df_daily_full = add_composites(df_raw.copy())
    df_daily_show = df_daily_full.tail(DAILY_BARS).reset_index(drop=True)
    sig_daily = detect_cross(df_daily_full)

    # 주봉: 리샘플 → 지표 계산 → 최근 30주만 표시
    df_weekly_full = resample_weekly(df_raw.copy())
    df_weekly_full = add_composites(df_weekly_full)
    df_weekly_show = df_weekly_full.tail(WEEKLY_BARS).reset_index(drop=True)
    sig_weekly = detect_cross(df_weekly_full)

    # ════════════ 1열 × 4행 차트 ════════════
    fig, (ax_dc, ax_di, ax_wc, ax_wi) = plt.subplots(
        nrows=4, ncols=1, figsize=(10, 14),
        gridspec_kw={'height_ratios': [3, 1, 3, 1], 'hspace': 0.35}
    )

    d_sig_txt = f"  [{sig_daily}]" if sig_daily else ""
    w_sig_txt = f"  [{sig_weekly}]" if sig_weekly else ""

    _plot_panel(ax_dc, ax_di, df_daily_show,
                title=f"일봉 {DAILY_BARS}일 — {code} ({name}){d_sig_txt}",
                date_fmt='%m/%d')

    _plot_panel(ax_wc, ax_wi, df_weekly_show,
                title=f"주봉 {WEEKLY_BARS}주 — {code} ({name}){w_sig_txt}",
                date_fmt='%y/%m')

    fig.suptitle(
        f"MACD+Stochastic  단기{FAST_PERIOD}/장기{SLOW_PERIOD}/"
        f"K1={K_WINDOW}/K2={K_SMOOTH}/D={D_SMOOTH}  "
        f"기준선 {OS_LINE}/{OB_LINE}",
        fontproperties=fontprop, fontsize=9, y=1.0, color='gray'
    )

    fig.tight_layout(rect=[0, 0, 1, 0.98])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)

    trading_str = f"{trading_value:,.0f}" if trading_value is not None else "-"
    msg = (
        f"{name} ({code})\n"
        f"외국인 순매수 거래대금: {trading_str}백만원\n"
        f"현재가: {today_close:,.0f}원 ({change:+,.0f} / {change_rate:+.2f}%)\n"
        f"일봉 신호: {sig_daily if sig_daily else '없음'}\n"
        f"주봉 신호: {sig_weekly if sig_weekly else '없음'}"
    )

    send_photo(buf.getvalue(), caption=msg)
    return sig_daily, sig_weekly

# ════════════════════════ 9) 메인 실행 ════════════════════════
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
    print(top10[["종목코드", "종목명", "외국인 순매수 거래대금"]])

    alerts = []
    for _, row in top10.iterrows():
        code = row["종목코드"]
        name = row["종목명"]
        tv   = row.get("외국인 순매수 거래대금")
        try:
            result = analyze_symbol(code, name, tv)
            if result:
                sd, sw = result
                if sd or sw:
                    alerts.append((code, name, sd or '-', sw or '-'))
        except Exception as e:
            logging.exception(f"{code}({name}) 분석 실패: {e}")

    if alerts:
        lines = [f"📈 오늘 신호 종목 ({len(alerts)}개)\n"]
        for c, n, sd, sw in alerts:
            lines.append(f"• {c} ({n}) — 일봉: {sd} / 주봉: {sw}")
        send_message("\n".join(lines))
    else:
        send_message("📭 오늘 신호 없음")

    logging.info("═══ 완료 ═══")

if __name__ == "__main__":
    main()
