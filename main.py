import os
import io
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 환경 변수
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FONT_PATH = 'fonts/NanumGothic.ttf'

# 한글 폰트 설정
if os.path.exists(FONT_PATH):
    font_prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = font_prop.get_name()

# KIS API 기본 URL
KIS_BASE_QUOT = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'

# MACD+Stoch 합성 지표 계산
def compute_indicators(df):
    exp1 = df['Close'].ewm(span=12).mean()
    exp2 = df['Close'].ewm(span=26).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9).mean()
    low14 = df['Low'].rolling(14).min()
    high14 = df['High'].rolling(14).max()
    stoch_k = 100 * (df['Close'] - low14) / (high14 - low14)
    stoch_d = stoch_k.rolling(3).mean()
    return pd.DataFrame({'CompK': macd + stoch_k, 'CompD': signal + stoch_d}).dropna()

# 교차 신호 계산
def compute_signals(df_ind):
    df = df_ind.copy()
    df['pK'], df['pD'] = df['CompK'].shift(), df['CompD'].shift()
    sigs = []
    for date, row in df.iterrows():
        if row['pK'] < row['pD'] and row['CompK'] > row['CompD']:
            sigs.append((date, 'buy'))
        if row['pK'] > row['pD'] and row['CompK'] < row['CompD']:
            sigs.append((date, 'sell'))
    return sigs

# 외국인/기관 공통 순매수 상위 종목 조회 함수
# API 포털에서 확인한 `foreign-institution-total` endpoint 사용

def get_common_net_buy(n=10):
    ep = 'foreign-institution-total'
    url = f"{KIS_BASE_QUOT}/{ep}"
    headers = {'Content-Type': 'application/json', 'appKey': KIS_APP_KEY, 'appSecret': KIS_APP_SECRET}
    params = {
        'CANO': KIS_ACCNO,
        'INQR_DVSN': '2',
        'INQR_DT': datetime.now().strftime('%Y%m%d'),
        'MAX_CNT': n
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"KIS API error (foreign-institution-total): {e}")
        return []
    try:
        data = r.json()
    except ValueError:
        print(f"JSON decode error (foreign-institution-total): {r.text}")
        return []
    items = data.get('output2') or data.get('output') or []
    return [itm.get('stck_shrn_iscd') for itm in items if itm.get('stck_shrn_iscd')]

# 텔레그램 전송 함수
def send_telegram(text, buf=None):
    bot = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if buf:
        files = {'photo': buf}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text}
        requests.post(bot + 'sendPhoto', data=data, files=files)
    else:
        requests.post(bot + 'sendMessage', data={'chat_id': TELEGRAM_CHAT_ID, 'text': text})

# 차트 생성 함수
def plot_signals(code, df, dfi, sigs):
    plt.style.use('dark_background')
    fig, axs = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={'height_ratios': [2, 1]})
    # 가격 차트
    axs[0].plot(df.index, df['Close'], linewidth=1.2)
    for date, typ in sigs:
        price = df.loc[date, 'Close']
        marker = '^' if typ == 'buy' else 'v'
        color = 'lime' if typ == 'buy' else 'red'
        axs[0].scatter(date, price, marker=marker, color=color)
    axs[0].set_title(f"{code} Price & Signals")
    axs[0].grid(True, linestyle='--', linewidth=0.5)
    # Composite 지표 차트
    axs[1].plot(dfi.index, dfi['CompK'], label='CompK', linewidth=1)
    axs[1].plot(dfi.index, dfi['CompD'], label='CompD', linewidth=1)
    axs[1].legend(loc='upper left')
    axs[1].grid(True, linestyle='--', linewidth=0.5)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# 메인 실행
if __name__ == '__main__':
    # 공통 순매수 종목 조회
    common = get_common_net_buy()
    if not common:
        send_telegram("공통 순매수 종목이 없습니다.")
    else:
        send_telegram(f"공통 순매수 종목: {', '.join(common)}")
        start_date = (datetime.now() - relativedelta(months=6)).strftime('%Y-%m-%d')
        for code in common:
            df = fdr.DataReader(code, start_date)
            dfi = compute_indicators(df)
            sigs = compute_signals(dfi)
            if sigs:
                buf = plot_signals(code, df, dfi, sigs)
                send_telegram(code, buf)
            else:
                send_telegram(f"{code}: 신호가 없습니다.")
