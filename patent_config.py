"""전력 이슈 특허 아카이브 — 설정.

뉴스와 같은 8개 카테고리를 쓰되, 특허 본문은 기술 용어 위주이므로 검색어를
한국어/영어로 분리한다(한국 특허=한글 검색, 미국 특허=영문 검색).

수집 주기는 '주 1회'다. 특허는 정기 공개(미국 화/목, 한국 공개공보)라 매일 돌릴
이유가 없다. 실행일이 속한 주(월요일 시작)로 묶어 누적한다.

경로/모드는 news_config 를 재사용해 뉴스와 같은 사이트에 함께 배포된다.
"""
from __future__ import annotations

import os

import news_config as ncfg

# 사이트 안에서 특허 원자료(JSON)가 담기는 하위 폴더 / 주별 페이지 폴더
PATENT_DATA_SUBDIR = "data/patents"
PATENT_WEEK_SUBDIR = "p"

COUNTRIES = ["KR", "US"]                 # 한국·미국
LOOKBACK_DAYS = int(os.getenv("PATENT_LOOKBACK_DAYS", "7"))
PER_QUERY_LIMIT = int(os.getenv("PATENT_PER_QUERY", "20"))
PER_CATEGORY_LIMIT = int(os.getenv("PATENT_PER_CATEGORY", "18"))
# 카테고리·국가별 상한(한국/미국 균형 수집). 카테고리당 최대 = 국가수 × 이 값.
PER_COUNTRY_LIMIT = int(os.getenv("PATENT_PER_COUNTRY", "10"))
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "25"))
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)   # auto | on | off

COUNTRY_LABEL = {"KR": ("🇰🇷", "한국"), "US": ("🇺🇸", "미국")}

# 카테고리별 특허 검색어 (뉴스와 같은 key/emoji/name, 검색어만 기술용어로 튜닝)
CATEGORIES = [
    # 검색은 제목 정확검색(q=TI="용어"). 용어는 '특허 제목에 실제로 등장하는 정밀 전력 용어'
    # 로 고른다 → 무선통신·AI 특허는 제목이 달라 배제. cpc 는 옵션(기본 미사용; 필요 시 AND 잠금).
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리",
     "kr": ["수요반응", "전력수요 예측", "피크저감"],
     "en": ["demand response", "peak shaving"], "cpc": ""},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
     "kr": ["송전선로", "변전소", "변압기", "보호계전기"],
     "en": ["power transmission", "substation", "power transformer"], "cpc": ""},
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
     "kr": ["소형모듈원자로", "원자력발전"],
     "en": ["small modular reactor", "nuclear reactor"], "cpc": ""},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장",
     "kr": ["에너지저장장치", "해상풍력", "태양광발전"],
     "en": ["energy storage system", "offshore wind", "photovoltaic"], "cpc": ""},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·전원장치",
     "kr": ["무정전전원장치", "데이터센터 전원"],
     "en": ["uninterruptible power supply", "data center power"], "cpc": ""},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·팹 전력",
     "kr": ["전력반도체", "전력변환장치"],
     "en": ["power semiconductor", "power converter"], "cpc": ""},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드",
     "kr": ["전력량계", "스마트그리드"],
     "en": ["smart meter", "smart grid"], "cpc": ""},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기",
     "kr": ["가스절연개폐장치", "초고압케이블", "차단기"],
     "en": ["gas insulated switchgear", "high voltage cable", "circuit breaker"], "cpc": ""},
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
