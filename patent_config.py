"""전력 이슈 특허 아카이브 — 설정 (출원인 중심 수집).

수집 축을 바꿨다: '분야 키워드로 20개' → '주요 출원인 × 분야 키워드' 교집합.
→ "어느 출원인이 어느 분야에 어떤 기술을 출원하나"를 분석할 수 있다.

무키 Google Patents xhr 로 다음을 실측 확인(프로브)했다:
  - assignee=<이름>&country=..&sort=new  → 해당 출원인 특허만 정확히 반환(한글명도 가능).
  - assignee 와 q=TI="<용어>" 를 함께 주면 '그 출원인의 그 분야' 특허로 좁혀진다.
  - KR 특허도 Google 에선 영문 제목으로 색인 → 분야 키워드는 영어 하나로 KR/US 공통.
  - 검색 결과에 CPC 는 없다 → 분야는 '검색에 쓴 키워드'로 태깅(질의=분류).

경로/모드는 news_config 를 재사용해 뉴스와 같은 사이트에 함께 배포된다.
"""
from __future__ import annotations

import os

import news_config as ncfg

# 사이트 안에서 특허 원자료(JSON)가 담기는 하위 폴더 / 주별 페이지 폴더
PATENT_DATA_SUBDIR = "data/patents"
PATENT_WEEK_SUBDIR = "p"

COUNTRIES = ["KR", "US"]                  # 공개청(특허청) 구분 라벨용
COUNTRY_LABEL = {"KR": ("🇰🇷", "한국"), "US": ("🇺🇸", "미국")}

# (출원인, 분야) 조합마다 최신 몇 건까지 담을지. 조합당 상한이라 '분야 활동 유무+표본'.
PER_PAIR_LIMIT = int(os.getenv("PATENT_PER_PAIR", "6"))
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "25"))
# 요청 사이 지연(초). 무키 엔드포인트는 ~100요청 넘으면 차단 → 넉넉히 둔다.
# (요청 수 = 출원인 수 × 분야당 용어 수. 아래 목록/키워드는 이 예산 안에서 관리.)
REQUEST_DELAY = float(os.getenv("PATENT_REQ_DELAY", "1.0"))
LOOKBACK_DAYS = int(os.getenv("PATENT_LOOKBACK_DAYS", "7"))   # MOCK 날짜 분산용
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)          # auto | on | off

# ── 주요 출원인(큐레이션) ────────────────────────────────────────
# name: 표시용 대표명 / iso: 국적(KR·US) — 이 값이 조회 country 이자 KR/US 구분
# q   : Google Patents assignee= 에 넣을 이름(영문 권장; 프로브로 매칭 확인)
# 편집 가능: 분석하고 싶은 출원인을 추가/삭제하면 된다.
# ★ 무키 엔드포인트는 한 실행에서 ~100요청을 넘으면 차단한다. 8열 매트릭스를 한 번에
#   채우려면 (출원인 수 × 분야 8) ≲ 90 이어야 해 → 핵심 12곳으로 압축(전력 전업 우선).
#   더 넣고 싶으면 다른 곳을 빼고 교체(요청 예산 안에서). 순서 = 수집 우선순위.
APPLICANTS = [
    # 미국 — 전력/원전/터빈/중전기
    {"name": "GE Vernova", "iso": "US", "q": "GE Vernova"},
    {"name": "General Electric", "iso": "US", "q": "General Electric"},
    {"name": "Westinghouse", "iso": "US", "q": "Westinghouse Electric"},
    {"name": "Eaton", "iso": "US", "q": "Eaton"},
    {"name": "Tesla", "iso": "US", "q": "Tesla"},
    # 한국 — 전력 유틸리티·중전기·케이블·원전
    {"name": "한국전력공사", "iso": "KR", "q": "Korea Electric Power"},
    {"name": "두산에너빌리티", "iso": "KR", "q": "Doosan Enerbility"},
    {"name": "효성중공업", "iso": "KR", "q": "Hyosung Heavy Industries"},
    {"name": "현대일렉트릭", "iso": "KR", "q": "Hyundai Electric"},
    {"name": "LS일렉트릭", "iso": "KR", "q": "LS Electric"},
    # 한국 — 배터리·대형 전자(전력 인접)
    {"name": "LG에너지솔루션", "iso": "KR", "q": "LG Energy Solution"},
    {"name": "삼성전자", "iso": "KR", "q": "Samsung Electronics"},
]

# 예산 초과로 이번엔 뺀 후보(원하면 위 목록과 교체): First Solar, 한국수력원자력,
# LS전선, 한국전기연구원, 삼성SDI, SK온, 한화솔루션, LG전자, 현대자동차, SK하이닉스 등.

# ── 분야(기술 카테고리) ──────────────────────────────────────────
# 뉴스와 같은 key/emoji/name 을 유지(색·라벨 공유). terms = 제목 정확검색용 영문 용어.
# 출원인과 AND 로 결합해 '그 출원인의 그 분야' 특허를 뽑는다(질의어가 곧 분야 태그).
# terms: 제목 정확검색용. 요청 예산(≈출원인수×용어수)을 위해 분야당 1개 위주,
# 커버리지가 갈리는 재생에너지만 2개(배터리 저장 + 태양광). '무선전력'류 오탐을 피하려
# 모호한 'power transmission' 대신 'power transmission line'(송전선로)로 좁혔다.
CATEGORIES = [
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리",
     "terms": ["demand response"]},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
     "terms": ["power transmission line"]},
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
     "terms": ["nuclear reactor"]},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장",
     "terms": ["energy storage"]},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·전원장치",
     "terms": ["uninterruptible power"]},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·전력변환",
     "terms": ["power semiconductor"]},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드",
     "terms": ["smart grid"]},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기",
     "terms": ["switchgear"]},
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
APPLICANT_BY_NAME = {a["name"]: a for a in APPLICANTS}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
