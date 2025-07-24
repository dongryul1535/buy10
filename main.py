import os
import io
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime
from dateutil.relativedelta import relativedelta
import urllib3

# 환경 변수 및 설정
SIMULATION = os.getenv('SIMULATION', 'false').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FONT_PATH = 'fonts/NanumGothic.ttf'

# KIS 인증 정보 (실계좌 / 모의계좌)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
if SIMULATION:
    # 모의투자용: production host with sandbox port (29443)
    KIS_BASE = 'http://openapi.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations'
    KIS_ACCNO = os.getenv('KIS_SIM_ACCOUNT_NUMBER')
else:
    # 실계좌용
    KIS_BASE = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'
    KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')

# 한글 폰트 설정
if os.path.exists(FONT_PATH):
    font_prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = font_prop.get_name()

# MACD+Stochastic 합성 지표 계산
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
def compute_signals(df):
    df2 = df.copy()
    df2['pK'], df2['pD'] = df2['CompK'].shift(), df2['CompD'].shift()
    sigs = []
    for idx, row in df2.iterrows():
        if row['pK'] < row['pD'] and row['CompK'] > row['CompD']:
            sigs.append((idx, 'buy'))
        elif row['pK'] > row['pD'] and row['CompK'] < row['CompD']:
            sigs.append((idx, 'sell'))
    return sigs

# 외국인/기관 별도 순매수 상위 종목 조회 함수
def get_top_net_buy(inv_type, n=10):
    """
    inv_type: 'foreign' or 'institution'
    """
    # 투자자 구분 코드
    div_code = '1000' if inv_type=='foreign' else '2000'
    url = f"{KIS_BASE}/investor-net"
    headers = {
        'Content-Type': 'application/json',
        'appKey': KIS_APP_KEY,
        'appSecret': KIS_APP_SECRET
    }
    params = {
        'CANO': KIS_ACCNO,
        'INQR_DVSN': '2',
        'INQR_DT': datetime.now().strftime('%Y%m%d'),
        'INVST_DIV_CODE': div_code,
        'MAX_CNT': n
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10, verify=(not SIMULATION))
        r.raise_for_status()
        data = r.json()
        items = data.get('output2') or data.get('output') or []
        return [itm.get('stck_shrn_iscd') for itm in items if itm.get('stck_shrn_iscd')]
    except Exception as e:
        print(f"Error in get_top_net_buy({inv_type}): {e}")
        return []

# 외국인/기관 공통 순매수 상위 종목 조회
# foreign-institution-total 호출에 실패하면 외국인/기관 별도 조회 후 교집합으로 처리

def get_common_net_buy(n=10):
    url = f"{KIS_BASE}/foreign-institution-total"
    headers = {
        'Content-Type': 'application/json',
        'appKey': KIS_APP_KEY,
        'appSecret': KIS_APP_SECRET
    }
    params = {
        'CANO': KIS_ACCNO,
        'INQR_DVSN': '2',
        'INQR_DT': datetime.now().strftime('%Y%m%d'),
        'MAX_CNT': n
    }
    # 시도: foreign-institution-total
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10, verify=(not SIMULATION))
        r.raise_for_status()
        data = r.json()
        items = data.get('output2') or data.get('output') or []
        codes = [itm.get('stck_shrn_iscd') for itm in items if itm.get('stck_shrn_iscd')]
        if codes:
            return codes
    except Exception as e:
        print(f"Primary API failed: {e}")
    # Fallback: 외국인, 기관 별도 조회 후 교집합
    print("Falling back to separate calls for foreign and institution orders")
    foreign = get_top_net_buy('foreign', n)
    institution = get_top_net_buy('institution', n)
    return sorted(set(foreign) & set(institution))

# 텔레그램 전송
def send_telegram(text, buf=None):
    bot_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if buf:
        files = {'photo': buf}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text}
        requests.post(bot_url + 'sendPhoto', data=data, files=files)
    else:
        requests.post(bot_url + 'sendMessage', data={'chat_id': TELEGRAM_CHAT_ID, 'text': text})

# 차트 생성
def plot_signals(code, df, dfi, sigs):
    plt.style.use('dark_background')
    fig, axs = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={'height_ratios': [2, 1]})
    axs[0].plot(df.index, df['Close'], linewidth=1.2)
    for date, typ in sigs:
        marker = '^' if typ == 'buy' else 'v'
        color = 'lime' if typ == 'buy' else 'red'
        axs[0].scatter(date, df.loc[date, 'Close'], marker=marker, color=color)
    axs[0].set_title(f"{code} Price & Signals{' (모의)' if SIMULATION else ''}")
    axs[0].grid(True, linestyle='--', linewidth=0.5)
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

# Main 실행
def main():
    common = get_common_net_buy()
    if not common:
        send_telegram("공통 순매수 종목이 없습니다.")
        return
    send_telegram(f"공통 순매수 종목{' (모의)' if SIMULATION else ''}: {', '.join(common)}")
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

if __name__ == '__main__':
    main()
