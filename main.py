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
import time
import logging
import requests
import pandas as pd
import FinanceDataReader as fdr
import matplotlib.pyplot as plt
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ──────────────────────────────────────────────────────────────────────────────
# 1) KIS API 인증
# ──────────────────────────────────────────────────────────────────────────────
API_KEY    = os.getenv("KIS_APP_KEY")
API_SECRET = os.getenv("KIS_APP_SECRET")
TOKEN_URL  = "https://openapi.koreainvestment.com:9443/oauth2/token"
_access_token = None

def auth():
    """KIS OpenAPI 토큰을 발급 받아 _access_token에 저장합니다."""
    global _access_token
    if not API_KEY or not API_SECRET:
        raise RuntimeError("환경변수 KIS_APP_KEY와 KIS_APP_SECRET을 설정해주세요.")
    data = {"grant_type": "client_credentials", "appkey": API_KEY, "appsecret": API_SECRET}
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    resp = requests.post(TOKEN_URL, data=data, headers=headers)
    if resp.status_code != 200:
        logging.error(f"토큰 발급 실패: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 응답 에러: {resp.text}")
    _access_token = token

# ──────────────────────────────────────────────────────────────────────────────
# 2) 외국인 매매종목가집계 조회
# ──────────────────────────────────────────────────────────────────────────────
API_URL = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/foreign-institution-total"
TR_ID   = "FHKSTA01400"
PARAMS = {
    "fid_cond_mrkt_div_code": "V",
    "fid_cond_scr_div_code": "16449",
    "fid_input_iscd": "0000",
    "fid_div_cls_code": "0",
    "fid_rank_sort_cls_code": "0",
    "fid_etc_cls_code": "0"
}

def fetch_top10_foreign() -> pd.DataFrame:
    """외국인 순매수 거래대금 상위 10종목을 DataFrame으로 반환합니다."""
    if not _access_token:
        raise RuntimeError("토큰이 없습니다. auth()를 먼저 호출하세요.")
    headers = {
        "Content-Type":  "application/json; charset=UTF-8",
        "Authorization": f"Bearer {_access_token}",
        "appkey":        API_KEY,
        "appsecret":     API_SECRET,
        "tr_id":         TR_ID,
        "custtype":      "P"
    }
    # 최대 3회 재시도
    for i in range(1, 4):
        resp = requests.post(API_URL, headers=headers, json=PARAMS, timeout=10)
        if resp.status_code == 200:
            break
        logging.warning(f"UAPI 요청 {i}회차 실패: {resp.status_code} {resp.text}")
        time.sleep(1)
    else:
        logging.error("UAPI 모든 시도 실패 - 빈 DataFrame 반환")
        return pd.DataFrame()

    body = resp.json()
    items = body.get("output", {}).get("foreignInstitutionTotals", [])
    df = pd.DataFrame(items)
    if df.empty:
        logging.warning("조회된 데이터가 없습니다.")
        return df
    df = df.rename(columns={
        'mksc_shrn_iscd':    '종목코드',
        'hts_kor_isnm':      '종목명',
        'frgn_ntby_tr_pbmn': '외국인 순매수 거래대금'
    })
    df['외국인 순매수 거래대금'] = pd.to_numeric(df['외국인 순매수 거래대금'], errors='coerce')
    return df.sort_values('외국인 순매수 거래대금', ascending=False).head(10)

# ──────────────────────────────────────────────────────────────────────────────
# 3) Telegram 알림
# ──────────────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
    raise RuntimeError("환경변수 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정해주세요.")
SEND_MSG_URL   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
SEND_PHOTO_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

def send_message(text: str):
    r = requests.post(SEND_MSG_URL, data={"chat_id": TELEGRAM_CHAT, "text": text})
    r.raise_for_status()

def send_photo(img_bytes: bytes, caption: str = ""):
    files = {"photo": ("chart.png", img_bytes)}
    data  = {"chat_id": TELEGRAM_CHAT, "caption": caption}
    r = requests.post(SEND_PHOTO_URL, files=files, data=data)
    r.raise_for_status()

# ──────────────────────────────────────────────────────────────────────────────
# 4) 지표 계산 및 시그널 탐지
# ──────────────────────────────────────────────────────────────────────────────
def analyze_symbo
