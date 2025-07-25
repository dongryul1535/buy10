#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py

KIS OpenAPI 인증 + 외국인 순매수 상위 10종목 조회
+ FinanceDataReader로 6개월치 가격 데이터 조회
+ NH MTS 스타일 Composite MACD+Stochastic 차트 작성
+ Golden/Dead Cross 감지 시 Telegram 알림

환경변수:
  KIS_APP_KEY, KIS_APP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import logging
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ──────────────────────────────────────────────────────────────────────────────
# 1) KIS API 인증 로직
# ──────────────────────────────────────────────────────────────────────────────
API_KEY    = os.getenv("KIS_APP_KEY")
API_SECRET = os.getenv("KIS_APP_SECRET")
TOKEN_URL  = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
_access_token = None

def auth():
    global _access_token
    if not API_KEY or not API_SECRET:
        raise RuntimeError("환경변수 KIS_APP_KEY/KIS_APP_SECRET 을 설정해주세요.")
    data = {"grant_type": "client_credentials", "appkey": API_KEY, "appsecret": API_SECRET}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(TOKEN_URL, data=data, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    _access_token = body.get("access_token")
    if not _access_token:
        raise RuntimeError(f"토큰 발급 실패: {body}")


def get_headers():
    if not _access_token:
        raise RuntimeError("토큰이 없습니다. 먼저 auth() 를 호출하세요.")
    return {"Authorization": f"Bearer {_access_token}", "Content-Type": "application/json"}

# ──────────────────────────────────────────────────────────────────────────────
# 2) 외국인 매매종목가집계 조회
# ──────────────────────────────────────────────────────────────────────────────
API_URL = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/foreign-institution-total"
PARAMS = dict(
    fid_cond_mrkt_div_code="V",  # 전체
    fid_cond_scr_div_code="16449",  # 전체
    fid_input_iscd="0000",  # 전체
    fid_div_cls_code="0",  # 전체
    fid_rank_sort_cls_code="0",  # 거래대금 기준
    fid_etc_cls_code="0"
)

def fetch_top10_foreign():
    headers = get_headers()
    resp = requests.get(API_URL, headers=headers, params=PARAMS)
    resp.raise_for_status()
    body = resp.json()
    items = body.get("output", {}).get("foreignInstitutionTotals", [])
    df = pd.DataFrame(items)
    df = df.rename(columns={
        'mksc_shrn_iscd': '종목코드',
        'hts_kor_isnm': '종목명',
        'frgn_ntby_tr_pbmn': '외국인 순매수 거래대금'
    })
    df['외국인 순매수 거래대금'] = pd.to_numeric(df['외국인 순매수 거래대금'], errors='coerce')
    return df.sort_values('외국인 순매수 거래대금', ascending=False).head(10).reset_index(drop=True)

# ──────────────────────────────────────────────────────────────────────────────
# 3) Telegram 알림 함수
# ──────────────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise RuntimeError("환경변수 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 을 설정해주세요.")

TELEGRAM_SENDMSG = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
TELEGRAM_SENDPHOTO = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"

def send_message(text: str):
    resp = requests.post(TELEGRAM_SENDMSG, data={"chat_id": CHAT_ID, "text": text})
    resp.raise_for_status()


def send_photo(image_bytes: bytes, caption: str = None):
    files = {"photo": ("chart.png", image_bytes)}
    data = {"chat_id": CHAT_ID}
    if caption:
        data["caption"] = caption
    resp = requests.post(TELEGRAM_SENDPHOTO, files=files, data=data)
    resp.raise_for_status()

# ──────────────────────────────────────────────────────────────────────────────
# 4) 지표 계산 및 신호 탐지 함수
# ──────────────────────────────────────────────────────────────────────────────

def analyze_symbol(code: str, name: str):
    # 가격 조회: 6개월
    end = datetime.today().date()
    start = end - relativedelta(months=6)
    df = fdr.DataReader(code, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    if df.empty:
        logging.warning(f"{code}({name}) 데이터 조회 실패")
        return

    # MACD
    ema_fast = df['Close'].ewm(span=12, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=26, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    
    # Stochastic
    low14 = df['Low'].rolling(window=14).min()
    high14 = df['High'].rolling(window=14).max()
    stoch_k = (df['Close'] - low14) / (high14 - low14) * 100
    stoch_d = stoch_k.rolling(window=3).mean()

    # Composite
    comp_k = macd + stoch_k
    comp_d = macd_signal + stoch_d

    # 신호 탐지 (마지막 두 일자)
    if len(comp_k) < 2:
        return
    prev_k, prev_d = comp_k.iloc[-2], comp_d.iloc[-2]
    curr_k, curr_d = comp_k.iloc[-1], comp_d.iloc[-1]

    signal = None
    if prev_k < prev_d and curr_k > curr_d:
        signal = 'BUY'
    elif prev_k > prev_d and curr_k < curr_d:
        signal = 'SELL'

    if not signal:
        return

    # 차트 그리기
    plt.figure(figsize=(10, 6))
    plt.plot(df.index, comp_k, label='Composite K')
    plt.plot(df.index, comp_d, label='Composite D')
    plt.title(f"{name}({code}) Composite MACD+Stoch")
    plt.legend()
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    # Telegram 전송
    caption = f"{name}({code}) - {signal} 신호 발생"
    send_photo(buf.getvalue(), caption=caption)
    send_message(f"{name}({code}): {signal} 신호를 보냈습니다.")

# ──────────────────────────────────────────────────────────────────────────────
# 5) 메인 흐름
# ──────────────────────────────────────────────────────────────────────────────

def main():
    logging.info("KIS API 인증 완료")
    auth()
    top10 = fetch_top10_foreign()

    print("\n=== 외국인 순매수 거래대금 상위 10종목 ===\n")
    print(top10[['종목코드', '종목명', '외국인 순매수 거래대금']])

    for _, row in top10.iterrows():
        analyze_symbol(row['종목코드'], row['종목명'])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
