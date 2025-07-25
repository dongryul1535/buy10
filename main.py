#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py

KIS OpenAPI 인증 + 외국인 순매수 상위 10종목 조회
+ FinanceDataReader로 6개월치 가격 데이터 조회
+ NH MTS 스타일 Composite MACD+Stochastic 차트 작성
+ Golden/Dead Cross 감지 시 Telegram 알림
+ 모든 날짜 연산을 한국 표준시(Asia/Seoul) 기준으로 처리

환경변수:
  KIS_APP_KEY, KIS_APP_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

필수 패키지:
  requests, pandas, FinanceDataReader, matplotlib, python-dateutil
선택 패키지 (타임존 처리):
  pytz (Python <3.9 환경에서 필요한 경우)
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

# 타임존 처리 (Python 3.9+ zoneinfo 또는 pytz)
try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo('Asia/Seoul')
except ImportError:
    import pytz
    KST = pytz.timezone('Asia/Seoul')

# ──────────────────────────────────────────────────────────────────────────────
# 5) 메인 실행
# ──────────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("1) KIS API 인증 시작")
    auth()
    logging.info("2) KIS API 인증 완료")
    top10 = fetch_top10_foreign()
    if top10.empty:
        logging.error("상위 종목 조회 실패, 프로그램 종료")
        return

    # 결과 출력
    print("
=== 외국인 순매수 거래대금 상위 10종목 ===
")
    print(top10[["종목코드", "종목명", "외국인 순매수 거래대금"]])

    # 각 종목별 시그널 분석
    for _, row in top10.iterrows():
        analyze_symbol(row["종목코드"], row["종목명"])

if __name__ == "__main__":
    main()
