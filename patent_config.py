"""전력 이슈 특허 아카이브 — 설정 (출원인 × 분야).

수집 축: 큐레이션한 주요 출원인 × 전력 CPC.
표시 축: 출원인을 **국적(미국·한국·중국·일본·유럽)별로 묶어** 매트릭스를 나눠 본다
        → "각 나라 주요 기업이 어느 분야에 무엇을 출원하나".
        ※ 이 그룹축은 '출원인 국적'이며, 특허가 실제로 공개된 특허청(office)과는
          다르다(한 기업이 US·EP·WO 등 여러 곳에 공개). office 는 카드에 표시한다.

수집은 EPO OPS 공식 API 를 쓴다(프로브로 실측 확인):
  - pa="<출원인>" and pd within "<기간>" and (cpc=...) → 출원인당 1~2요청.
  - 응답에 CPC 가 있어 분야를 코드로 분류한다(제목 키워드 추정보다 정확).
  - 중/일/유럽 특허도 영문 서지로 정규화돼 돌아온다.

경로/모드는 news_config 를 재사용해 뉴스와 같은 사이트에 함께 배포된다.
"""
from __future__ import annotations

import os

import news_config as ncfg

# 사이트 안에서 특허 원자료(JSON)가 담기는 하위 폴더
PATENT_DATA_SUBDIR = "data/patents"

# ── 지역(그룹) 정의 — 매트릭스를 이 순서로 나눠 표시 ──────────────
REGIONS = ["US", "KR", "CN", "JP", "EU"]
REGION_LABEL = {
    "US": ("🇺🇸", "미국"), "KR": ("🇰🇷", "한국"), "CN": ("🇨🇳", "중국"),
    "JP": ("🇯🇵", "일본"), "EU": ("🇪🇺", "유럽"),
}
# (구)코드 호환: site_render 가 참조하던 이름 유지
COUNTRIES = REGIONS
COUNTRY_LABEL = REGION_LABEL

# ── 공개 특허청(시장) ─────────────────────────────────────────────
# "어느 시장에 출원했나" 축. 출원인 국적(REGIONS)과는 다른 개념이며, 같은 발명이
# 여러 특허청에 공개되므로 특허청별 합계는 출원인 총계를 넘을 수 있다.
# OPS CQL: pn any "<코드>" (프로브로 실측 확인)
OFFICES = [
    {"code": "US", "emoji": "🇺🇸", "name": "미국"},
    {"code": "KR", "emoji": "🇰🇷", "name": "한국"},
    {"code": "CN", "emoji": "🇨🇳", "name": "중국"},
    {"code": "JP", "emoji": "🇯🇵", "name": "일본"},
    {"code": "EP", "emoji": "🇪🇺", "name": "유럽(EPO)"},
    {"code": "WO", "emoji": "🌐", "name": "PCT 국제"},
]

# ── 수집(EPO OPS) ────────────────────────────────────────────────
# 출원인마다 '최근 N일 발행 + 전력 CPC' 특허를 한 번에 받아 CPC로 분야를 분류한다.
# (무키 시절의 출원인×분야 개별 조회가 아니라 출원인당 1~2요청 → 회전 불필요)
OPS_KEY = os.getenv("OPS_KEY", "")
OPS_SECRET = os.getenv("OPS_SECRET", "")
PER_APPLICANT_LIMIT = int(os.getenv("PATENT_PER_APPLICANT", "50"))  # 출원인당 최대 수집
LOOKBACK_DAYS = int(os.getenv("PATENT_LOOKBACK_DAYS", "90"))        # 최근 N일 발행분
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "40"))
REQUEST_DELAY = float(os.getenv("PATENT_REQ_DELAY", "0.4"))         # OPS 쿼터 배려
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)                # auto | on | off
# 출원인×특허청 정확 집계(공개국별 랭킹). 전 출원인을 한 번에 돌리면 OPS 무료 쿼터를
# 넘어 403 이 나고 본 수집까지 망친다(실측) → **매일 뉴스 실행에 얹어** 일부만 돌리고
# data/patents/stats.json 에 병합해 누적한다. 8곳/일이면 31곳을 4일에 한 바퀴 돈다
# (요청량 = 8 × (1 총계 + 6 특허청) = 56/일).
OFFICE_COUNTS = os.getenv("PATENT_OFFICE_COUNTS", "on") != "off"
OFFICE_BATCH = int(os.getenv("PATENT_OFFICE_BATCH", "8"))   # 한 실행에서 처리할 출원인 수
# 집계는 '있으면 좋은' 부가 정보다. OPS 가 느려도 매일 도는 뉴스 배포를 붙잡으면 안 되므로
# 전체 소요에 상한을 두고, 넘으면 그 자리에서 접고 다음 날 이어서 채운다.
OFFICE_BUDGET = float(os.getenv("PATENT_OFFICE_BUDGET", "180"))   # 초
OFFICE_TIMEOUT = int(os.getenv("PATENT_OFFICE_TIMEOUT", "15"))    # 집계 요청 1건 타임아웃(초)

# ── 주요 출원인(큐레이션) ────────────────────────────────────────
# name: 표시명 / region: 그룹(미국·한국·중국·일본·유럽) / flag: 행 국기(국적)
# q   : OPS pa= 검색어. 문자열 하나 또는 여러 표기의 목록(목록이면 OR 로 묶어 조회).
# 편집 가능: 지역별로 자유롭게 추가/삭제(OPS 는 출원인당 1~2요청이라 여유 있음).
# ※ ✔ 는 실수집으로 결과가 확인된 곳.
# ※ 2026-07-27 프로브로 전 출원인 검색어를 실측 확인했다. Dynapower·산일전기·제룡전기는
#   '검색어가 틀린 것이 아니라'(각각 45·56·56건이 색인에 있음) 최근 90일 전력 CPC 공개가
#   0건이라 안 잡힌다 → 그대로 두고, 공개가 생기면 자동으로 들어온다.
APPLICANTS = [
    # 🇺🇸 미국
    {"name": "General Electric", "region": "US", "flag": "🇺🇸", "q": "General Electric"},      # ✔
    {"name": "GE Vernova", "region": "US", "flag": "🇺🇸", "q": "GE Vernova"},                  # ✔
    {"name": "Eaton", "region": "US", "flag": "🇺🇸", "q": "Eaton"},                            # ✔
    {"name": "Caterpillar", "region": "US", "flag": "🇺🇸", "q": "Caterpillar"},
    {"name": "Dynapower", "region": "US", "flag": "🇺🇸", "q": "Dynapower"},   # ✔ 검색어 확인(최근 공개 0)
    {"name": "Westinghouse", "region": "US", "flag": "🇺🇸", "q": "Westinghouse Electric"},
    {"name": "Kairos Power", "region": "US", "flag": "🇺🇸", "q": "Kairos Power"},
    # 🇰🇷 한국
    {"name": "한국전력공사", "region": "KR", "flag": "🇰🇷", "q": "Korea Electric Power"},          # ✔
    {"name": "한국전력기술", "region": "KR", "flag": "🇰🇷", "q": "KEPCO Engineering Construction"},
    {"name": "HD현대일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "Hyundai Electric"},            # ✔
    {"name": "효성중공업", "region": "KR", "flag": "🇰🇷", "q": "Hyosung Heavy Industries"},       # ✔
    # 2022년 두산중공업 → 두산에너빌리티로 사명 변경. 변경 전 출원이 옛 이름으로 남아 둘 다 조회.
    {"name": "두산에너빌리티", "region": "KR", "flag": "🇰🇷",
     "q": ["Doosan Enerbility", "Doosan Heavy Industries"]},
    {"name": "LS일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "LS Electric"},                    # ✔
    {"name": "삼성전자", "region": "KR", "flag": "🇰🇷", "q": "Samsung Electronics"},              # ✔
    {"name": "LG에너지솔루션", "region": "KR", "flag": "🇰🇷", "q": "LG Energy Solution"},
    {"name": "삼성SDI", "region": "KR", "flag": "🇰🇷", "q": "Samsung SDI"},
    {"name": "일진전기", "region": "KR", "flag": "🇰🇷", "q": "Iljin Electric"},
    {"name": "대한전선", "region": "KR", "flag": "🇰🇷", "q": "Taihan"},
    {"name": "산일전기", "region": "KR", "flag": "🇰🇷",
     "q": ["Sanil Electric", "산일전기"]},          # ✔ 검색어 확인(최근 공개 0)
    {"name": "제룡전기", "region": "KR", "flag": "🇰🇷",
     "q": ["Jeryong", "제룡전기"]},                 # ✔ 검색어 확인(최근 공개 0)
    {"name": "그리드위즈", "region": "KR", "flag": "🇰🇷", "q": "Gridwiz"},
    # 🇨🇳 중국
    {"name": "State Grid", "region": "CN", "flag": "🇨🇳", "q": "State Grid Corporation of China"},  # ✔
    {"name": "Huawei", "region": "CN", "flag": "🇨🇳", "q": "Huawei"},                          # ✔
    {"name": "CATL", "region": "CN", "flag": "🇨🇳", "q": "Contemporary Amperex Technology"},   # ✔
    {"name": "CNNC", "region": "CN", "flag": "🇨🇳", "q": "China National Nuclear"},   # 중국핵공업집단
    # 🇯🇵 일본
    {"name": "Hitachi Energy", "region": "JP", "flag": "🇯🇵", "q": "Hitachi Energy"},          # ✔
    # 히타치제작소(Hitachi, Ltd.). 그냥 "Hitachi" 로 두면 위 Hitachi Energy 까지 걸려
    # 총계가 부풀려지므로 어구로 묶어 분리한다(목록 중복은 공개번호 dedup 이 막지만
    # stats 의 총계는 출원인별 독립 질의라 겹치면 그대로 부푼다).
    {"name": "Hitachi", "region": "JP", "flag": "🇯🇵", "q": "Hitachi Ltd"},
    {"name": "Mitsubishi Electric", "region": "JP", "flag": "🇯🇵", "q": "Mitsubishi Electric"},# ✔
    {"name": "Toshiba", "region": "JP", "flag": "🇯🇵", "q": "Toshiba"},                        # ✔
    {"name": "Panasonic", "region": "JP", "flag": "🇯🇵", "q": "Panasonic"},                    # ✔
    {"name": "Kyocera", "region": "JP", "flag": "🇯🇵", "q": "Kyocera"},
    {"name": "Toyota", "region": "JP", "flag": "🇯🇵", "q": "Toyota"},
    {"name": "Sumitomo Electric", "region": "JP", "flag": "🇯🇵", "q": "Sumitomo Electric"},    # ✔
    {"name": "Furukawa Electric", "region": "JP", "flag": "🇯🇵", "q": "Furukawa Electric"},
    # 🇪🇺 유럽
    {"name": "Siemens", "region": "EU", "flag": "🇩🇪", "q": "Siemens"},                        # ✔
    {"name": "ABB", "region": "EU", "flag": "🇨🇭", "q": "ABB"},                                # ✔
    {"name": "Schneider Electric", "region": "EU", "flag": "🇫🇷", "q": "Schneider Electric"},  # ✔
    {"name": "Bosch", "region": "EU", "flag": "🇩🇪", "q": "Robert Bosch"},
    {"name": "Vestas", "region": "EU", "flag": "🇩🇰", "q": "Vestas Wind Systems"},
]

# ── 분야(기술 카테고리) — CPC 분류 기반 ────────────────────────────
# EPO OPS 는 CPC 를 주므로, 제목 키워드 대신 CPC 로 검색·분류한다(정확도↑).
#   cpc   : OPS CQL 검색에 쓸 CPC 접두(여러 개면 OR)
#   match : 응답 CPC 를 분야로 되분류할 때 쓰는 접두(우선순위는 CATEGORIES 순서)
# 전력 분야 CPC 요약:
#   H02J 전력 공급/분배 계통   H02M 전력 변환   H01L 반도체(전력소자)
#   H01H 개폐기·차단기        H02B 배전반      H01F 변압기
#   G21 원자력                H02S 태양광      H01M 전지(저장)
#   G01R 계측(전력량계)       Y04S 스마트그리드 ICT   Y02E 에너지 감축기술
CATEGORIES = [
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
     "cpc": ["G21C", "G21D"], "match": ["G21"]},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장",
     "cpc": ["H02S", "H01M10", "F03D"], "match": ["H02S", "F03D", "H01M"]},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드",
     "cpc": ["Y04S", "G01R21", "G01R22"], "match": ["Y04S", "G01R21", "G01R22"]},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·무정전전원",
     "cpc": ["H02J9"], "match": ["H02J9"]},
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리",
     "cpc": ["H02J3"], "match": ["H02J3", "H02J13"]},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·전력변환",
     "cpc": ["H02M", "H01L29"], "match": ["H02M", "H01L", "H03K17"]},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기",
     "cpc": ["H01H33", "H02B"], "match": ["H01H", "H02B"]},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
     "cpc": ["H02G", "H01F27", "H02J1"], "match": ["H02G", "H01F", "H02J"]},
]

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
APPLICANT_BY_NAME = {a["name"]: a for a in APPLICANTS}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
