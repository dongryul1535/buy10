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
KIS_BASE = 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations'
INVESTORS = {'foreign': '1000', 'institution': '2000'}

# MACD+Stoch 합성 지표
def compute_indicators(df):
    exp1 = df['Close'].ewm(span=12).mean()
    exp2 = df['Close'].ewm(span=26).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9).mean()
    low14 = df['Low'].rolling(14).min()
    high14 = df['High'].rolling(14).max()
    stoch_k = 100*(df['Close']-low14)/(high14-low14)
    stoch_d = stoch_k.rolling(3).mean()
    return pd.DataFrame({'CompK': macd+stoch_k, 'CompD': signal+stoch_d}).dropna()

# 교차 신호
def compute_signals(df_ind):
    df = df_ind.copy()
    df['pK'], df['pD'] = df['CompK'].shift(), df['CompD'].shift()
    sigs=[]
    for date,row in df.iterrows():
        if row['pK']<row['pD'] and row['CompK']>row['CompD']:
            sigs.append((date,'buy'))
        if row['pK']>row['pD'] and row['CompK']<row['CompD']:
            sigs.append((date,'sell'))
    return sigs

# 순매수 상위 종목 조회 (외국인/기관 별도 endpoint)
def get_top_net_buy(inv_type, inv_code, n=10):
    # endpoint 결정
    if inv_type=='foreign': ep='investor-foreign-net'
    elif inv_type=='institution': ep='investor-institution-net'
    else: ep='investor-net'
    url=f"{KIS_BASE}/{ep}"
    headers={'Content-Type':'application/json','appKey':KIS_APP_KEY,'appSecret':KIS_APP_SECRET}
    params={'CANO':KIS_ACCNO,'INQR_DVSN':'2','INQR_DT':datetime.now().strftime('%Y%m%d'),
            'INVST_DIV_CODE':inv_code,'MAX_CNT':n}
    try:
        r=requests.get(url,headers=headers,params=params,timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"KIS API error ({ep}): {e}")
        return []
    try:
        data=r.json()
    except ValueError:
        print(f"JSON decode error ({ep}): {r.text}")
        return []
    output=data.get('output2') or []
    return [itm.get('stck_shrn_iscd') for itm in output if itm.get('stck_shrn_iscd')]

# 텔레그램 전송
def send_telegram(text, buf=None):
    bot=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if buf:
        files={'photo':buf};data={'chat_id':TELEGRAM_CHAT_ID,'caption':text}
        requests.post(bot+'sendPhoto',data=data,files=files)
    else:
        requests.post(bot+'sendMessage',data={'chat_id':TELEGRAM_CHAT_ID,'text':text})

# 차트 생성
def plot_signals(code, df, dfi, sigs):
    plt.style.use('dark_background')
    fig,axs=plt.subplots(2,1,figsize=(8,6),gridspec_kw={'height_ratios':[2,1]})
    axs[0].plot(df.index,df['Close'],lw=1.2)
    for d,t in sigs:
        m='^' if t=='buy' else 'v';c='lime' if t=='buy' else 'red'
        axs[0].scatter(d,df.loc[d,'Close'],marker=m,color=c)
    axs[0].set_title(f"{code} Price & Signals")
    axs[0].grid(True,ls='--',lw=0.5)
    axs[1].plot(dfi.index,dfi['CompK'],label='CompK',lw=1)
    axs[1].plot(dfi.index,dfi['CompD'],label='CompD',lw=1)
    axs[1].legend(loc='upper left');axs[1].grid(True,ls='--',lw=0.5)
    plt.tight_layout()
    buf=io.BytesIO();plt.savefig(buf,format='png',dpi=150);buf.seek(0);plt.close(fig)
    return buf

# 메인
if __name__=='__main__':
    f_codes=set(get_top_net_buy('foreign',INVESTORS['foreign']))
    i_codes=set(get_top_net_buy('institution',INVESTORS['institution']))
    common=sorted(f_codes&i_codes)
    if not common: send_telegram("공통 종목이 없습니다.")
    else:
        send_telegram(f"공통 종목: {', '.join(common)}")
        start=(datetime.now()-relativedelta(months=6)).strftime('%Y-%m-%d')
        for c in common:
            df=fdr.DataReader(c,start)
            dfi=compute_indicators(df);sigs=compute_signals(dfi)
            if sigs: send_telegram(c,plot_signals(c,df,dfi,sigs))
            else: send_telegram(f"{c}: 신호 없음.")
