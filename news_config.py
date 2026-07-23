"""전력 뉴스 아카이브 — 설정.

수집 대상(전력 이슈 전반)을 '카테고리 → 검색어/이모지' 로 정의한다.
카테고리마다 Google 뉴스 RSS 를 한 번씩 조회하므로, 하루 요청 수 = 카테고리 수.

경로는 환경변수로 덮어쓸 수 있어 GitHub Actions(누적 배포)와 로컬(미리보기)에서
같은 코드로 동작한다:
  - NEWS_SITE_DIR : 정적 사이트를 쓸 폴더 (기본 site/)
  - NEWS_PREV_DIR : 이전 아카이브가 담긴 폴더 (기본 없음 → SITE_DIR 재사용)
  - NEWS_MOCK     : auto | on | off (기본 auto — 네트워크 차단/오류 시 자동 MOCK)
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent

# ── 사이트/데이터 경로 ────────────────────────────────────────────
SITE_DIR = Path(os.getenv("NEWS_SITE_DIR", str(BASE_DIR / "site")))
# 이전 실행이 만든 아카이브(JSON)를 읽어올 폴더. Actions에선 gh-pages 체크아웃 경로.
_prev = os.getenv("NEWS_PREV_DIR", "")
PREV_DIR = Path(_prev) if _prev else None
DATA_SUBDIR = "data"          # 사이트 안에서 원자료(JSON)가 담기는 하위 폴더
DAY_SUBDIR = "d"              # 날짜별 상세 페이지가 담기는 하위 폴더

# ── 동작 옵션 ────────────────────────────────────────────────────
MOCK_MODE = os.getenv("NEWS_MOCK", "auto")     # auto | on | off
PER_CATEGORY_LIMIT = int(os.getenv("NEWS_PER_CATEGORY", "14"))
REQUEST_TIMEOUT = int(os.getenv("NEWS_TIMEOUT", "20"))
# 최근 며칠 이내 기사만 '오늘 새로 발견'으로 인정(오래된 재탕 방지). 0=무제한.
FRESH_DAYS = int(os.getenv("NEWS_FRESH_DAYS", "0"))

SITE_TITLE = os.getenv("NEWS_SITE_TITLE", "전력 이슈 뉴스 아카이브")
SITE_TAGLINE = os.getenv(
    "NEWS_SITE_TAGLINE",
    "반도체 클러스터·AI 데이터센터·3대 메가프로젝트 시대의 전력 이슈를 매일 모읍니다")

# ── 카테고리 정의 ────────────────────────────────────────────────
# key: 내부 식별자 / emoji / name / queries: RSS 검색어(OR 결합)
CATEGORIES = [
    {
        "key": "supply", "emoji": "⚡", "name": "전력수급·전력난",
        "queries": ["전력난", "전력수급", "전력 예비율", "최대전력 수요", "전력 대란"],
    },
    {
        "key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
        "queries": ["송전선로", "변전소", "전력망 확충", "계통연계", "HVDC", "동해안 송전"],
    },
    {
        "key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
        "queries": ["원전", "원자력발전", "SMR 소형모듈원전", "신한울"],
    },
    {
        "key": "renew", "emoji": "🌿", "name": "재생에너지",
        "queries": ["해상풍력", "태양광 발전", "재생에너지 계통", "ESS 에너지저장"],
    },
    {
        "key": "datacenter", "emoji": "🖥️", "name": "데이터센터·AI 전력",
        "queries": ["데이터센터 전력", "AI 전력 수요", "데이터센터 전력난"],
    },
    {
        "key": "mega", "emoji": "🏗️", "name": "반도체 클러스터·메가프로젝트",
        "queries": ["용인 반도체 전력", "반도체 클러스터 전력", "국가첨단전략산업 전력"],
    },
    {
        "key": "policy", "emoji": "🏛️", "name": "전기요금·정책·한전",
        "queries": ["전기요금", "한국전력 실적", "전력수급기본계획", "한전 적자"],
    },
    {
        "key": "industry", "emoji": "🏭", "name": "전력설비·산업",
        "queries": ["초고압 케이블", "변압기 수출", "전력기기", "전선 수출"],
    },
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}


def is_mock() -> bool:
    """MOCK 여부. auto 이면 네트워크 시도 후 실패하면 news_source 에서 폴백한다."""
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
