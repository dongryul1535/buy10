import os
import io
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime
from dateutil.relativedelta import relativedelta

# 환경 변수 가져오기
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FONT_PATH = 'fonts/NanumGothic.ttf'  # 한글 폰트 경로

# 한글 폰트 설정
if os.path.exists(FONT_PATH):
    font_prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = font_prop.get_name()

# KIS API 기본 정보
KIS_BASE_URL = 'https://openapi.koreainvestment.com:9443'

# 투자자 구분 코드
INVESTORS = {'foreign': '1000', 'institution': '2000'}

# MACD+Stoch 합성 지표 계산
def compute_indicators(df):
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    low14 = df['Low'].rolling(14).min()
    high14 = df['High'].rolling(14).max()
    stoch_k = 100 * (df['Close'] - low14) / (high14 - low14)
    stoch_d = stoch_k.rolling(3).mean()
    comp_k = macd + stoch_k
    comp_d = signal + stoch_d
    return pd.DataFrame({'CompK': comp_k, 'CompD': comp_d}).dropna()

# 교차 신호 계산
def compute_signals(df_ind):
    df = df_ind.copy()
    df['prevK'] = df['CompK'].shift(1)
    df['prevD'] = df['CompD'].shift(1)
    signals = []
    for idx, row in df.iterrows():
        if row['prevK'] < row['prevD'] and row['CompK'] > row['CompD']:
            signals.append((idx, 'buy'))
        if row['prevK'] > row['prevD'] and row['CompK'] < row['CompD']:
            signals.append((idx, 'sell'))
    return signals

# KIS API: 순매수 상위 종목 조회
def get_top_net_buy(inv_code, top_n=10):
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/investor-net"
    headers = {'Content-Type': 'application/json', 'appKey': KIS_APP_KEY, 'appSecret': KIS_APP_SECRET}
    params = {'CANO': KIS_ACCNO, 'INQR_DVSN': '2', 'INQR_DT': datetime.now().strftime('%Y%m%d'),
              'INVST_DIV_CODE': inv_code, 'MAX_CNT': top_n}
    resp = requests.get(url, headers=headers, params=params)
    items = resp.json().get('output2', [])
    return [itm['stck_shrn_iscd'] for itm in items]

# Telegram 메시지 전송
def send_telegram(text, photo_buf=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if photo_buf:
        files = {'photo': photo_buf}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text}
        requests.post(url + 'sendPhoto', data=data, files=files)
    else:
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': text}
        requests.post(url + 'sendMessage', data=data)

# 차트 생성 및 전송
def plot_signals(code, df_price, df_ind, signals):
    plt.style.use('dark_background')
    fig, axs = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={'height_ratios': [2,1]})
    # 가격
    axs[0].plot(df_price.index, df_price['Close'], linewidth=1.2)
    for date, typ in signals:
        price = df_price.loc[date, 'Close']
        color = 'lime' if typ=='buy' else 'red'
        marker = '^' if typ=='buy' else 'v'
        axs[0].scatter(date, price, color=color, marker=marker)
    axs[0].set_title(f"{code} Price & Signals")
    axs[0].grid(True, linestyle='--', linewidth=0.5)
    # Composite
    axs[1].plot(df_ind.index, df_ind['CompK'], label='CompK', linewidth=1)
    axs[1].plot(df_ind.index, df_ind['CompD'], label='CompD', linewidth=1)
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
    foreign = set(get_top_net_buy(INVESTORS['foreign']))
    institution = set(get_top_net_buy(INVESTORS['institution']))
    common = sorted(foreign & institution)
    if not common:
        send_telegram("외국인·기관 공통 순매수 종목이 없습니다.")
    else:
        send_telegram(f"공통 순매수 종목: {', '.join(common)}")
        start = (datetime.now() - relativedelta(months=6)).strftime('%Y-%m-%d')
        for code in common:
            df = fdr.DataReader(code, start)
            df_ind = compute_indicators(df)
            sigs = compute_signals(df_ind)
            if sigs:
                buf = plot_signals(code, df, df_ind, sigs)
                send_telegram(code, photo_buf=buf)
            else:
                send_telegram(f"{code}: 최근 6개월 내 교차 신호 없음.")
