"""(임시) 미집계 출원인의 OPS 검색어(pa=) 후보를 실측한다.

두 가지를 따로 센다:
  A. 이름만        pa="<후보>"                     → OPS 색인에 그 이름이 있나?
  B. 실제 수집 조건 pa=... and pd within ... and cpc → 최근 90일 전력 특허가 있나?

A=0 이면 검색어가 틀린 것이고, A>0 이면서 B=0 이면 이름은 맞는데 해당 기간에
전력 CPC 공개가 없는 것이다(버그 아님). 확인 후 이 파일과 워크플로는 지운다.
"""
from __future__ import annotations

import time
from datetime import datetime

import patent_config as cfg
import patent_source as ps

DELAY = 2.5      # OPS 분당 스로틀 회피(간격 없이 쏘면 403 이 쏟아진다)


def count(token: str, cql: str):
    """403 이면 한 번 쉬고 재시도. 404 는 '0건'."""
    for attempt in (1, 2):
        try:
            n = ps._count(token, cql)
            time.sleep(DELAY)
            return n
        except Exception as e:
            if "404" in str(e):
                time.sleep(DELAY)
                return 0
            if "403" in str(e) and attempt == 1:
                time.sleep(30)
                continue
            time.sleep(DELAY)
            return f"err({e})"

CANDIDATES = {
    "Sumitomo Electric (대조군)": ["Sumitomo Electric"],
    "Dynapower": ["Dynapower Company", "Dynapower", "Dynapower Company LLC",
                  "Sensata Technologies"],
    "산일전기": ["Sanil", "Sanil Electric", "SANIL ELECTRIC CO LTD",
              "Sanil Electric Co", "산일전기"],
    "제룡전기": ["Jeryong", "Jeryong Electric", "JERYONG ELECTRIC CO LTD",
              "Je Ryong Electric", "제룡전기"],
}


def main() -> None:
    if not (cfg.OPS_KEY and cfg.OPS_SECRET):
        raise SystemExit("OPS 키 없음")
    token = ps._get_token()
    today = datetime.now()
    for label, qs in CANDIDATES.items():
        print(f"\n== {label}")
        for q in qs:
            a = count(token, f'pa="{q}"')
            b = count(token, ps._cql(q, cfg.LOOKBACK_DAYS, today))
            print(f'  pa="{q}"  →  이름 전체 {a} · 최근90일+전력CPC {b}', flush=True)


if __name__ == "__main__":
    main()
