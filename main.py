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

# KIS API 설정
KIS_BASE = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'
KIS_APP_KEY = os.getenv('KIS_APP_KEY')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET')
KIS_ACCNO = os.getenv('KIS_ACCOUNT_NUMBER')

# HTTP 세션 및 재시도 설정
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# 한글 폰트 설정
if os.path.exists(FONT_PATH):
    prop = fm.FontProperties(fname=FONT_PATH)
    plt.rcParams['font.family'] = prop.get_name()

# OAuth2 토큰 발급 함수
OAUTH_URL = 'https://openapi.koreainvestment.com:9443/oauth2/token'
def get_access_token():
    """클라이언트 자격 증명 방식으로 액세스 토큰을 발급받습니다."""
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
    print(f"DEBUG: token={token}")
    return token

# 국내기관·외국인 매매종목 가집계 조회
AGG_PATH = 'foreign-institution-total'
def get_aggregated_codes(max_cnt=10):
    """국내기관·외국인 매매종목 가집계 API 호출 후 종목 코드 리스트 반환"""
    token = get_access_token()
    url = f"{KIS_BASE}/{AGG_PATH}"
    headers = {
        'Content-Type': 'application/json',
        'appKey': KIS_APP_KEY,
        'appSecret': KIS_APP_SECRET,
        'Authorization': f'Bearer {token}'
    }
    payload = {
        'fid_cond_mrkt_div_code': 'V',    # 시장 구분 (V: Default)
        'fid_cond_scr_div_code': '16449', # 스크리닝 코드
        'fid_input_iscd': '0000',         # 전체 종목
        'fid_div_cls_code': '0',          # 0:수량정렬, 1:금액정렬
        'fid_rank_sort_cls_code': '0',    # 0:순매수상위, 1:순매도상위
        'fid_etc_cls_code': '0'           # 0:전체,1:외국인,2:기관계,3:기타
    }
    try:
        # 집계 API는 GET 방식으로 호출합니다
        resp = session.get(url, headers=headers, params=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        items = data.get('output', []) or data.get('output2', [])
        return [itm['mksc_shrn_iscd'] for itm in items]
    except Exception as e:
        print(f"Error fetching aggregated codes: {e}")
        return []

# MACD+Stochastic 계산
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

# 매수/매도 신호 계산
def compute_signals(ind):
    prev = ind.shift(1)
    sigs = []
    for t in ind.index[1:]:
        pk, pd = prev.at[t, 'CompK'], prev.at[t, 'CompD']
        ck, cd = ind.at[t, 'CompK'], ind.at[t, 'CompD']
        if pk < pd and ck > cd:
            sigs.append((t, 'buy'))
        elif pk > pd and ck < cd:
            sigs.append((t, 'sell'))
    return sigs

# 차트 생성
def plot_signals(code, df, ind, sigs):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), gridspec_kw={'height_ratios': [2, 1]})
    ax1.plot(df.index, df['Close'], lw=1.2)
    for t, typ in sigs:
        marker = '^' if typ == 'buy' else 'v'
        color = 'lime' if typ == 'buy' else 'red'
        ax1.scatter(t, df.at[t, 'Close'], marker=marker, color=color)
    ax1.set_title(f"{code} Price & Signals")
    ax1.grid(True, ls='--', lw=0.5)
    ax2.plot(ind.index, ind['CompK'], lw=1, label='CompK')
    ax2.plot(ind.index, ind['CompD'], lw=1, label='CompD')
    ax2.legend(loc='upper left')
    ax2.grid(True, ls='--', lw=0.5)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# 텔레그램 발송
def send_telegram(text, buf=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    try:
        if buf:
            session.post(url + 'sendPhoto', data={'chat_id': TELEGRAM_CHAT_ID, 'caption': text}, files={'photo': buf})
        else:
            session.post(url + 'sendMessage', data={'chat_id': TELEGRAM_CHAT_ID, 'text': text})
    except Exception as e:
        print(f"Telegram send error: {e}")

# 메인 실행
if __name__ == '__main__':
    codes = get_aggregated_codes(10)
    if not codes:
        send_telegram('종목없음')
        exit()
    send_telegram(f"Codes: {', '.join(codes)}")
    start_date = (datetime.now() - relativedelta(months=6)).strftime('%Y-%m-%d')
    for code in codes:
        df = fdr.DataReader(code, start_date)
        ind = compute_indicators(df)
        sigs = compute_signals(ind)
        if sigs:
            buf = plot_signals(code, df, ind, sigs)
            send_telegram(code, buf)
        else:
            send_telegram(f"{code}: No signals")
