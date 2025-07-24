# Bot Script: kis_fdr_macd_stoch_telegram.py
```python
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
    exp1 = df['Close'].ewm(span=12).mean()
    exp2 = df['Close'].ewm(span=26).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9).mean()
    low14 = df['Low'].rolling(14).min()
    high14 = df['High'].rolling(14).max()
    stoch_k = 100*(df['Close']-low14)/(high14-low14)
    stoch_d = stoch_k.rolling(3).mean()
    comp_k, comp_d = macd+stoch_k, signal+stoch_d
    return pd.DataFrame({'CompK':comp_k,'CompD':comp_d}).dropna()

# 교차 신호 계산
def compute_signals(df_ind):
    df = df_ind.copy()
    df['pK'], df['pD'] = df['CompK'].shift(), df['CompD'].shift()
    sigs = []
    for date,row in df.iterrows():
        if row['pK']<row['pD'] and row['CompK']>row['CompD']:
            sigs.append((date,'buy'))
        if row['pK']>row['pD'] and row['CompK']<row['CompD']:
            sigs.append((date,'sell'))
    return sigs

# 순매수 상위 종목 조회
def get_top_net_buy(inv_code, n=10):
    url=f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/investor-net"
    h={'Content-Type':'application/json','appKey':KIS_APP_KEY,'appSecret':KIS_APP_SECRET}
    p={'CANO':KIS_ACCNO,'INQR_DVSN':'2','INQR_DT':datetime.now().strftime('%Y%m%d'),
       'INVST_DIV_CODE':inv_code,'MAX_CNT':n}
    out=requests.get(url,headers=h,params=p).json().get('output2',[])
    return [i['stck_shrn_iscd'] for i in out]

# 텔레그램 전송
def send_telegram(txt,buf=None):
    bot=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/"
    if buf:
        files={'photo':buf};data={'chat_id':TELEGRAM_CHAT_ID,'caption':txt}
        requests.post(bot+'sendPhoto',data=data,files=files)
    else:
        requests.post(bot+'sendMessage',data={'chat_id':TELEGRAM_CHAT_ID,'text':txt})

# 차트 생성
def plot_signals(code,df,dfi,sigs):
    plt.style.use('dark_background')
    fig,axs=plt.subplots(2,1,figsize=(8,6), gridspec_kw={'height_ratios':[2,1]})
    axs[0].plot(df.index,df['Close'],linewidth=1.2)
    for date,typ in sigs:
        m='^' if typ=='buy' else 'v';c='lime' if typ=='buy' else 'red'
        axs[0].scatter(date,df.loc[date,'Close'],marker=m,color=c)
    axs[0].set_title(f"{code} Price & Signals")
    axs[0].grid(True,ls='--',lw=0.5)
    axs[1].plot(dfi.index,dfi['CompK'],label='CompK');axs[1].plot(dfi.index,dfi['CompD'],label='CompD')
    axs[1].legend();axs[1].grid(True,ls='--',lw=0.5)
    plt.tight_layout()
    buf=io.BytesIO();plt.savefig(buf,format='png',dpi=150);buf.seek(0);plt.close(fig)
    return buf

if __name__=='__main__':
    f=set(get_top_net_buy(INVESTORS['foreign']));i=set(get_top_net_buy(INVESTORS['institution']))
    common=sorted(f&i)
    if not common: send_telegram("공통 종목 없음.")
    else:
        send_telegram(f"공통 종목: {', '.join(common)}")
        start=(datetime.now()-relativedelta(months=6)).strftime('%Y-%m-%d')
        for c in common:
            df=fdr.DataReader(c,start);dfi=compute_indicators(df);sigs=compute_signals(dfi)
            if sigs: send_telegram(c,plot_signals(c,df,dfi,sigs))
            else: send_telegram(f"{c}: 신호 없음.")
```

---

# requirements.txt
```
finance-datareader>=0.9.59
pandas
matplotlib
python-dateutil
requests
```

---

# GitHub Actions Workflow (`.github/workflows/run-bot.yml`)
```yaml
name: Run MACD+Stoch Bot

on:
  schedule:
    - cron: '15 1 * * 1-5'  # 평일 10:15 KST (UTC 01:15)
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Run bot script
        run: python kis_fdr_macd_stoch_telegram.py
        env:
          KIS_APP_KEY: ${{ secrets.KIS_APP_KEY }}
          KIS_APP_SECRET: ${{ secrets.KIS_APP_SECRET }}
          KIS_ACCOUNT_NUMBER: ${{ secrets.KIS_ACCOUNT_NUMBER }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```
