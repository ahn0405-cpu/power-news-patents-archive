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

# ── 동작 옵션 ────────────────────────────────────────────────────
MOCK_MODE = os.getenv("NEWS_MOCK", "auto")     # auto | on | off
PER_CATEGORY_LIMIT = int(os.getenv("NEWS_PER_CATEGORY", "14"))
REQUEST_TIMEOUT = int(os.getenv("NEWS_TIMEOUT", "20"))
# 유사 기사(같은 사건, 다른 매체) 제거 임계값(제목 2-gram 자카드). 높을수록 관대.
DEDUP_SIM = float(os.getenv("NEWS_DEDUP_SIM", "0.55"))

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
        "key": "policy", "emoji": "🏛️", "name": "전기요금·정책",
        "queries": ["전기요금", "한국전력 실적", "전력수급기본계획", "한전 적자"],
    },
    {
        "key": "industry", "emoji": "🏭", "name": "전력설비·산업",
        "queries": ["초고압 케이블", "변압기 수출", "전력기기", "전선 수출"],
    },
    # ── 아래 셋은 특허 분야를 Y04S 로 다시 세우면서 뒤늦게 채운 자리다 ──
    # 특허 쪽에는 수송(1,547건)·시장거래(980건)·통신(51건)이 들어와 있는데
    # 뉴스 쪽에는 그 검색어가 아예 없었다. 뉴스는 **검색어로만** 모으므로,
    # 없으면 안 모이고 안 모이면 없는 셈이 된다 — 실측으로 확인했다:
    # 뉴스 1,760건 중 전기차·충전 기사는 3건뿐이었고 그 셋마저 데이터센터
    # 기사가 '전기차' 를 스쳐 언급한 것이었다(진짜 충전·V2G 기사는 0건).
    #
    # 순서는 맨 뒤다. 앞 카테고리가 먼저 가져가므로, 여기 두면 기존 분류의
    # 몫을 빼앗지 않고 **어디에도 안 걸리던 기사만** 새로 들어온다.
    {
        "key": "ev", "emoji": "🚗", "name": "전기차·충전 인프라",
        "queries": ["전기차 충전", "충전 인프라", "급속충전기", "충전소 구축",
                    "V2G 양방향 충전", "전기버스 충전"],
    },
    {
        "key": "market", "emoji": "💱", "name": "전력거래·가상발전소",
        "queries": ["전력거래소", "전력시장 제도", "가상발전소", "수요반응 DR",
                    "전력중개사업", "재생에너지 PPA"],
    },
    {
        "key": "ict", "emoji": "🛡️", "name": "전력망 통신·보안",
        "queries": ["전력망 사이버보안", "스마트그리드 통신", "원격검침 AMI",
                    "전력 제어시스템 보안"],
    },
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}


def is_mock() -> bool:
    """MOCK 여부. auto 이면 네트워크 시도 후 실패하면 news_source 에서 폴백한다."""
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
