# main.py
import os
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 환경 변수 설정
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FONT_PATH = 'fonts/NanumGothic.ttf'

# KIS 실계좌 API 설정
KIS_BASE = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')

# HTTP 세션 및 재시도 설정
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.mount('http://', HTTPAdapter(max_retries=retries))

# 한글 폰트 설정
if os.path.exists(FONT_PATH):
    prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = prop.get_name()

# MACD+Stochastic 계산
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

# Golden/Dead Cross 신호
def compute_signals(df):
    df2 = df.copy()
    df2['pK'] = df2['CompK'].shift()
    df2['pD'] = df2['CompD'].shift()
    signals = []
    for idx, row in df2.iterrows():
        if row['pK'] < row['pD'] and row['CompK'] > row['CompD']:
            signals.append((idx, 'buy'))
        elif row['pK'] > row['pD'] and row['CompK'] < row['CompD']:
            signals.append((idx, 'sell'))
    return signals

# 환경 변수: 투자자 순매수 엔드포인트 경로
INVESTOR_NET_PATH = os.getenv('INVESTOR_NET_PATH', 'investor-net')  # API Portal Service Path로 설정

# 투자자 순매수 조회 (외국인 또는 기관)
def get_top_net_buy(inv_div_code, count=10):
    url = f"{KIS_BASE}/{INVESTOR_NET_PATH}"
    headers = {'Content-Type': 'application/json', 'appKey': KIS_APP_KEY, 'appSecret': KIS_APP_SECRET}
    params = {
        'CANO': KIS_ACCNO,
        'INQR_DVSN': '2',
        'INQR_DT': datetime.now().strftime('%Y%m%d'),
        'INVST_DIV_CODE': inv_div_code,
        'MAX_CNT': count
    }
    try:
        r = session.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 404:
            print(f"Endpoint not found: {INVESTOR_NET_PATH} (404)")
            return []
        r.raise_for_status()
        data = r.json()
        items = data.get('output2', [])
        return [item['stck_shrn_iscd'] for item in items]
    except Exception as e:
        print(f"Error fetching {INVESTOR_NET_PATH} ({inv_div_code}): {e}")
        return []

# 외국인/기관 교집합 종목
def get_common_net_buy(count=10):
    foreign = get_top_net_buy('1000', count)
    institution = get_top_net_buy('2000', count)
    return sorted(set(foreign) & set(institution))

# 텔레그램 전송
def send_telegram(message, buf=None):
    bot = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if buf:
            files = {'photo': buf}
            data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': message}
            session.post(bot + 'sendPhoto', data=data, files=files)
        else:
            session.post(bot + 'sendMessage', data={'chat_id': TELEGRAM_CHAT_ID, 'text': message})
    except Exception as e:
        print(f"Telegram error: {e}")

# 차트 생성
def plot_signals(code, df, df_ind, signals):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={'height_ratios': [2, 1]})
    ax1.plot(df.index, df['Close'], lw=1.2)
    for date, typ in signals:
        price = df.loc[date, 'Close']
        marker, color = ('^', 'lime') if typ == 'buy' else ('v', 'red')
        ax1.scatter(date, price, marker=marker, color=color)
    ax1.set_title(f"{code} Price & Signals")
    ax1.grid(True, ls='--', lw=0.5)
    ax2.plot(df_ind.index, df_ind['CompK'], lw=1, label='CompK')
    ax2.plot(df_ind.index, df_ind['CompD'], lw=1, label='CompD')
    ax2.legend(loc='upper left')
    ax2.grid(True, ls='--', lw=0.5)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# 메인 실행
def main():
    codes = get_common_net_buy()
    if not codes:
        send_telegram("공통 순매수 종목이 없습니다.")
        return
    send_telegram(f"공통 순매수 종목: {', '.join(codes)}")
    start = (datetime.now() - relativedelta(months=6)).strftime('%Y-%m-%d')
    for code in codes:
        df = fdr.DataReader(code, start)
        df_ind = compute_indicators(df)
        sigs = compute_signals(df_ind)
        if sigs:
            img = plot_signals(code, df, df_ind, sigs)
            send_telegram(code, img)
        else:
            send_telegram(f"{code}: 신호가 없습니다.")

if __name__ == '__main__':
    main()
