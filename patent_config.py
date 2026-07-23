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
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "25"))
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)   # auto | on | off

COUNTRY_LABEL = {"KR": ("🇰🇷", "한국"), "US": ("🇺🇸", "미국")}

# 카테고리별 특허 검색어 (뉴스와 같은 key/emoji/name, 검색어만 기술용어로 튜닝)
CATEGORIES = [
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리",
     "kr": ["전력수요관리", "수요반응"], "en": ["demand response", "power demand management"]},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
     "kr": ["송전 장치", "변전", "전력계통", "변압기"],
     "en": ["power transmission", "substation", "power grid", "transformer"]},
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
     "kr": ["소형모듈원자로", "원자로 냉각"], "en": ["small modular reactor", "nuclear reactor"]},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장",
     "kr": ["에너지저장장치", "태양광 발전", "풍력 발전"],
     "en": ["energy storage system", "photovoltaic power", "wind turbine"]},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·전원장치",
     "kr": ["데이터센터 전력", "무정전 전원장치"],
     "en": ["data center power", "uninterruptible power supply"]},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·팹 전력",
     "kr": ["전력반도체", "반도체 전력 공급"],
     "en": ["power semiconductor", "semiconductor fabrication power"]},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드",
     "kr": ["전력량계", "스마트미터", "스마트그리드"],
     "en": ["smart meter", "power metering", "smart grid"]},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기",
     "kr": ["초고압 케이블", "차단기", "개폐기"],
     "en": ["high voltage cable", "circuit breaker", "switchgear"]},
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
