# main.py
import os
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# 환경 변수
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
FONT_PATH = 'fonts/NanumGothic.ttf'

# KIS API 설정
KIS_BASE = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')

# HTTP 세션 재시도 설정
session = requests.Session()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500,502,503,504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# 한글 폰트 적용
if os.path.exists(FONT_PATH):
    prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = prop.get_name()

# OAuth2 Access Token 발급 (Client Credentials)
OAUTH_URL = 'https://openapi.koreainvestment.com:9443/oauth2/tokenP'

def get_access_token():
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'client_credentials',
        'appkey': KIS_APP_KEY,
        'appsecret': KIS_APP_SECRET
    }
    resp = session.post(OAUTH_URL, headers=headers, data=data)
    resp.raise_for_status()
    token_data = resp.json()
    token = token_data.get('access_token') or token_data.get('accessToken')
    print(f"DEBUG: access_token -> {token}")
    return token

# 종목 코드 조회: 국내기관·외국인 매매종목 가집계
AGG_PATH = 'foreign-institution-total'

def get_aggregated_codes(max_cnt=10):
    token = get_access_token()
    url = f"{KIS_BASE}/{AGG_PATH}"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
        'appKey': KIS_APP_KEY,
        'appSecret': KIS_APP_SECRET
    }
    params = {
        'FID_COND_MRKT_DIV_CODE': 'J',   # J: 전체, V: 기본값 (시장 구분)
        'FID_COND_SCR_DIV_CODE': '16449',# 스크리닝 코드 (16449 기본)
        'FID_INPUT_ISCD': '0000',        # 전종목
        'FID_DIV_CLS_CODE': '1',         # 0:수량, 1:금액
        'FID_RANK_SORT_CLS_CODE': '0',   # 0:순매수 상위, 1:순매도 상위
        'FID_ETC_CLS_CODE': '0',         # 0:전체, 1:외국인, 2:기관계, 3:기타
        'FID_PERIOD_DIV_CODE': '2',      # 1:당일, 2:누적 (사내 규격)
        'FID_ORG_ADJ_PRC': '0',          # 옵션 (0 기본)
        'FID_MAXCNT': str(max_cnt)
    }
    try:
        r = session.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get('output', []) or data.get('output2', [])
        return [item['mksc_shrn_iscd'] for item in items]
    except Exception as e:
        print(f"Error fetching aggregated codes: {e}")
        return [](max_cnt=10):
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
        'MAX_CNT': max_cnt
    }
    try:
        r = session.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get('output2', [])
        return [item['mksc_shrn_iscd'] for item in items]
    except Exception as e:
        print(f"Error fetching aggregated codes: {e}")
        return []

# 지표 계산: MACD + Stochastic 합성
def compute_indicators(df):
    exp12 = df['Close'].ewm(span=12).mean()
    exp26 = df['Close'].ewm(span=26).mean()
    macd = exp12 - exp26
    signal = macd.ewm(span=9).mean()
    low14 = df['Low'].rolling(14).min()
    high14 = df['High'].rolling(14).max()
    stoch_k = 100 * (df['Close'] - low14) / (high14 - low14)
    stoch_d = stoch_k.rolling(3).mean()
    return pd.DataFrame({'CompK': macd + stoch_k, 'CompD': signal + stoch_d}).dropna()

# 신호 계산: 교차 시점
def compute_signals(ind):
    ind_shift = ind.shift(1)
    signals = []
    for idx in ind.index[1:]:
        if ind_shift.at[idx,'CompK'] < ind_shift.at[idx,'CompD'] and ind.at[idx,'CompK'] > ind.at[idx,'CompD']:
            signals.append((idx,'buy'))
        elif ind_shift.at[idx,'CompK'] > ind_shift.at[idx,'CompD'] and ind.at[idx,'CompK'] < ind.at[idx,'CompD']:
            signals.append((idx,'sell'))
    return signals

# 차트 생성 및 버퍼 반환
def plot_signals(code, df, ind, sigs):
    plt.style.use('dark_background')
    fig, (ax1,ax2) = plt.subplots(2,1,figsize=(8,6), gridspec_kw={'height_ratios':[2,1]})
    ax1.plot(df.index, df['Close'], lw=1.2)
    for date,typ in sigs:
        marker='^' if typ=='buy' else 'v'
        color='lime' if typ=='buy' else 'red'
        ax1.scatter(date, df.at[date,'Close'], marker=marker, color=color)
    ax1.set_title(f"{code} Price & Signals")
    ax1.grid(True,ls='--',lw=0.5)
    ax2.plot(ind.index, ind['CompK'], lw=1, label='CompK')
    ax2.plot(ind.index, ind['CompD'], lw=1, label='CompD')
    ax2.legend(loc='upper left')
    ax2.grid(True,ls='--',lw=0.5)
    plt.tight_layout()
    buf=io.BytesIO()
    plt.savefig(buf,format='png',dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# 텔레그램 전송
def send_telegram(text, buf=None):
    url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if buf:
            files={'photo':buf}
            data={'chat_id':TELEGRAM_CHAT_ID,'caption':text}
            session.post(url+'sendPhoto',data=data,files=files)
        else:
            session.post(url+'sendMessage',data={'chat_id':TELEGRAM_CHAT_ID,'text':text})
    except Exception as e:
        print(f"Telegram send error: {e}")

# 메인 실행
def main():
    codes = get_aggregated_codes(10)
    if not codes:
        send_telegram("공통 순매수 종목이 없습니다.")
        return
    send_telegram(f"Aggregated Codes: {', '.join(codes)}")
    start = (datetime.now()-relativedelta(months=6)).strftime('%Y-%m-%d')
    for code in codes:
        df = fdr.DataReader(code, start)
        ind = compute_indicators(df)
        sigs = compute_signals(ind)
        if sigs:
            buf = plot_signals(code, df, ind, sigs)
            send_telegram(code, buf)
        else:
            send_telegram(f"{code}: No signals.")

if __name__=='__main__':
    main()
