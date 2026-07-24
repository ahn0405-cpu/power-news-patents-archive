"""전력 이슈 특허 아카이브 — 설정 (출원인 × 분야, 발행국/지역별).

수집 축: 큐레이션한 주요 출원인 × 8개 전력 분야 키워드 교집합.
표시 축: 출원인을 **지역(미국·한국·중국·일본·유럽)별로 묶어** 매트릭스를 나눠 본다
        → "각 나라 주요 출원인이 어느 분야에 무엇을 출원하나".

무키 Google Patents xhr 로 실측 확인(프로브):
  - assignee=<이름> & q=TI="<용어>" & sort=new (country 미지정) → 전 세계 공개에서
    해당 출원인·분야 특허를 정확히, 영문 제목으로 반환(중/일/유럽 특허도 영문 색인).
  - 검색 결과에 CPC 는 없다 → 분야는 '검색에 쓴 키워드'로 태깅(질의=분류).

경로/모드는 news_config 를 재사용해 뉴스와 같은 사이트에 함께 배포된다.
"""
from __future__ import annotations

import os

import news_config as ncfg

# 사이트 안에서 특허 원자료(JSON)가 담기는 하위 폴더 / 주별 페이지 폴더
PATENT_DATA_SUBDIR = "data/patents"
PATENT_WEEK_SUBDIR = "p"

# ── 지역(그룹) 정의 — 매트릭스를 이 순서로 나눠 표시 ──────────────
REGIONS = ["US", "KR", "CN", "JP", "EU"]
REGION_LABEL = {
    "US": ("🇺🇸", "미국"), "KR": ("🇰🇷", "한국"), "CN": ("🇨🇳", "중국"),
    "JP": ("🇯🇵", "일본"), "EU": ("🇪🇺", "유럽"),
}
# (구)코드 호환: site_render 가 참조하던 이름 유지
COUNTRIES = REGIONS
COUNTRY_LABEL = REGION_LABEL

# (출원인, 분야) 조합마다 담을 최신 특허 수(조합당 상한 = '분야 활동 유무 + 표본').
PER_PAIR_LIMIT = int(os.getenv("PATENT_PER_PAIR", "6"))
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "25"))
# 무키 엔드포인트는 실행당 ~100요청 뒤 차단 → 넉넉히 지연. (요청수 = 출원인수 × 분야수)
REQUEST_DELAY = float(os.getenv("PATENT_REQ_DELAY", "1.0"))
LOOKBACK_DAYS = int(os.getenv("PATENT_LOOKBACK_DAYS", "7"))   # MOCK 날짜 분산용
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)          # auto | on | off

# ── 주요 출원인(큐레이션) ────────────────────────────────────────
# name: 표시명 / region: 그룹(미국·한국·중국·일본·유럽) / flag: 행 국기(국적)
# q   : Google Patents assignee= 검색어(영문; 프로브로 매칭 확인)
# 편집 가능: 지역별로 추가/삭제. 요청 예산(출원인수 × 8) 은 ~100 이하 권장.
APPLICANTS = [
    # 🇺🇸 미국
    {"name": "General Electric", "region": "US", "flag": "🇺🇸", "q": "General Electric"},
    {"name": "GE Vernova", "region": "US", "flag": "🇺🇸", "q": "GE Vernova"},
    # 🇰🇷 한국
    {"name": "한국전력공사", "region": "KR", "flag": "🇰🇷", "q": "Korea Electric Power"},
    {"name": "한국전력기술", "region": "KR", "flag": "🇰🇷", "q": "KEPCO Engineering Construction"},
    {"name": "HD현대일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "Hyundai Electric"},
    {"name": "효성중공업", "region": "KR", "flag": "🇰🇷", "q": "Hyosung Heavy Industries"},
    {"name": "LS일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "LS Electric"},
    {"name": "삼성전자", "region": "KR", "flag": "🇰🇷", "q": "Samsung Electronics"},
    # 🇨🇳 중국
    {"name": "State Grid", "region": "CN", "flag": "🇨🇳", "q": "State Grid Corporation of China"},
    {"name": "Huawei", "region": "CN", "flag": "🇨🇳", "q": "Huawei"},
    {"name": "CATL", "region": "CN", "flag": "🇨🇳", "q": "Contemporary Amperex Technology"},
    # 🇯🇵 일본
    {"name": "Mitsubishi Electric", "region": "JP", "flag": "🇯🇵", "q": "Mitsubishi Electric"},
    # 🇪🇺 유럽
    {"name": "Siemens", "region": "EU", "flag": "🇩🇪", "q": "Siemens"},
    {"name": "ABB", "region": "EU", "flag": "🇨🇭", "q": "ABB"},
    {"name": "Schneider Electric", "region": "EU", "flag": "🇫🇷", "q": "Schneider Electric"},
]

# ── 분야(기술 카테고리) — 제목 정확검색용 영문 용어(분야당 1개, 무선전력 오탐 회피) ──
CATEGORIES = [
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리", "terms": ["demand response"]},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망", "terms": ["power transmission line"]},
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR", "terms": ["nuclear reactor"]},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장", "terms": ["energy storage"]},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·전원장치", "terms": ["uninterruptible power"]},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·전력변환", "terms": ["power semiconductor"]},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드", "terms": ["smart grid"]},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기", "terms": ["switchgear"]},
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
APPLICANT_BY_NAME = {a["name"]: a for a in APPLICANTS}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
