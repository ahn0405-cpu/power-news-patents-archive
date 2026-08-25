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
# 출원인당 최대 수집. 50 → 30 으로 낮췄다. 실측(8/3) 상 쿼터에 걸리기 전까지
# 24곳밖에 못 돌았는데, 상한이 낮으면 같은 쿼터로 더 많은 출원인에 닿는다
# (24곳 × 50 ≈ 40곳 × 30). 표본이 작아지면 분야 구성비 추정이 거칠어지지만,
# 규모는 표본이 아니라 OPS 총계에서 오므로(경쟁 구도 지표) 영향이 제한적이다.
PER_APPLICANT_LIMIT = int(os.getenv("PATENT_PER_APPLICANT", "30"))
LOOKBACK_DAYS = int(os.getenv("PATENT_LOOKBACK_DAYS", "90"))        # 최근 N일 발행분
REQUEST_TIMEOUT = int(os.getenv("PATENT_TIMEOUT", "40"))
REQUEST_DELAY = float(os.getenv("PATENT_REQ_DELAY", "0.4"))         # OPS 쿼터 배려
MOCK_MODE = os.getenv("PATENT_MOCK", ncfg.MOCK_MODE)                # auto | on | off
# 주간 수집의 시작점 회전 폭(출원인 수). 목록을 늘 같은 순서로 돌면 쿼터(403)가
# 나는 지점이 매주 같아 뒤쪽이 영구히 굶는다 — 실측: 8/3 실행은 39번(중국 끝)에서
# 끊겨 일본·유럽 25곳이 통째로 빠졌고, 세 주 내내 같은 자리에서 끊겼다. 한 실행이
# 소화하는 양(24~28곳)만큼 매주 시작점을 옮기면 3주에 한 바퀴가 돈다.
COLLECT_ROTATE = int(os.getenv("PATENT_COLLECT_ROTATE", "24"))
# 출원인×특허청 정확 집계(공개국별 랭킹). 전 출원인을 한 번에 돌리면 OPS 무료 쿼터를
# 넘어 403 이 나고 본 수집까지 망친다(실측) → **매일 뉴스 실행에 얹어** 일부만 돌리고
# data/patents/stats.json 에 병합해 누적한다. 8곳/일이면 31곳을 4일에 한 바퀴 돈다
# (요청량 = 8 × (1 총계 + 6 특허청) = 56/일).
OFFICE_COUNTS = os.getenv("PATENT_OFFICE_COUNTS", "on") != "off"
OFFICE_BATCH = int(os.getenv("PATENT_OFFICE_BATCH", "11"))  # 한 실행에서 처리할 출원인 수
# (출원인이 40→65곳으로 늘며 8곳/일이면 한 바퀴에 8일이 걸린다. 11곳/일이면 6일.)
# 집계는 '있으면 좋은' 부가 정보다. OPS 가 느려도 매일 도는 뉴스 배포를 붙잡으면 안 되므로
# 전체 소요에 상한을 두고, 넘으면 그 자리에서 접고 다음 날 이어서 채운다.
OFFICE_BUDGET = float(os.getenv("PATENT_OFFICE_BUDGET", "180"))   # 초
OFFICE_TIMEOUT = int(os.getenv("PATENT_OFFICE_TIMEOUT", "15"))    # 집계 요청 1건 타임아웃(초)
# 해외 출원인의 '국내(KR) 공개' 전용 수집.
# 본 수집은 출원인당 상한(PER_APPLICANT_LIMIT) 안에서 최신순이라 KR 공개가 뒤로 밀려난다 — 실측으로
# CATL 은 집계상 KR 60건인데 표본에는 0건이었다. 해외 출원인이 한국에 낸 건 그들이
# 한국 시장에서 지킬 값어치가 있다고 본 기술이라 따로 한 번 더 훑는다(pn any "KR").
KR_FOCUS = os.getenv("PATENT_KR_FOCUS", "on") != "off"
KR_LIMIT = int(os.getenv("PATENT_KR_LIMIT", "15"))          # 해외 출원인당 최대 수집
KR_BUDGET = float(os.getenv("PATENT_KR_BUDGET", "240"))     # 초, 넘으면 접고 다음 주에

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
    {"name": "NuScale", "region": "US", "flag": "🇺🇸", "q": "NuScale"},
    {"name": "TerraPower", "region": "US", "flag": "🇺🇸", "q": "TerraPower"},
    {"name": "Vertiv", "region": "US", "flag": "🇺🇸", "q": "Vertiv"},   # 데이터센터 전원
    # 🇰🇷 한국
    {"name": "한국전력공사", "region": "KR", "flag": "🇰🇷", "q": "Korea Electric Power"},          # ✔
    {"name": "한국전력기술", "region": "KR", "flag": "🇰🇷", "q": "KEPCO Engineering Construction"},
    # 영문 표기가 "KOREA HYDRO & NUCLEAR POWER" 라 '&' 때문에 어구가 끊길 수 있어
    # 앞부분만 쓰고, 한글 원표기도 함께 묶는다(산일전기 사례 — 한글로만 잡히는 문서가 있다).
    {"name": "한국수력원자력", "region": "KR", "flag": "🇰🇷",
     "q": ["Korea Hydro", "한국수력원자력"]},
    {"name": "HD현대일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "Hyundai Electric"},            # ✔
    {"name": "효성중공업", "region": "KR", "flag": "🇰🇷", "q": "Hyosung Heavy Industries"},       # ✔
    # 2022년 두산중공업 → 두산에너빌리티로 사명 변경. 변경 전 출원이 옛 이름으로 남아 둘 다 조회.
    {"name": "두산에너빌리티", "region": "KR", "flag": "🇰🇷",
     "q": ["Doosan Enerbility", "Doosan Heavy Industries"]},
    {"name": "LS일렉트릭", "region": "KR", "flag": "🇰🇷", "q": "LS Electric"},                    # ✔
    {"name": "삼성전자", "region": "KR", "flag": "🇰🇷", "q": "Samsung Electronics"},              # ✔
    {"name": "LG에너지솔루션", "region": "KR", "flag": "🇰🇷", "q": "LG Energy Solution"},
    {"name": "삼성SDI", "region": "KR", "flag": "🇰🇷", "q": "Samsung SDI"},
    {"name": "SK온", "region": "KR", "flag": "🇰🇷", "q": ["SK On", "에스케이온"]},
    {"name": "일진전기", "region": "KR", "flag": "🇰🇷", "q": "Iljin Electric"},
    {"name": "대한전선", "region": "KR", "flag": "🇰🇷", "q": "Taihan"},
    # 해저·초고압 케이블. 대한전선만 있고 빠져 있었다(정규화 별칭 표에는 등록돼 있었음).
    {"name": "LS전선", "region": "KR", "flag": "🇰🇷", "q": ["LS Cable", "엘에스전선"]},
    {"name": "한화솔루션", "region": "KR", "flag": "🇰🇷", "q": ["Hanwha Solutions", "한화솔루션"]},
    {"name": "씨에스윈드", "region": "KR", "flag": "🇰🇷", "q": ["CS Wind", "씨에스윈드"]},
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
    {"name": "CGN", "region": "CN", "flag": "🇨🇳", "q": "China General Nuclear"},     # 중국광핵집단
    {"name": "Sungrow", "region": "CN", "flag": "🇨🇳", "q": "Sungrow"},               # 인버터·ESS PCS
    {"name": "TBEA", "region": "CN", "flag": "🇨🇳", "q": "TBEA"},                     # 변압기
    {"name": "Goldwind", "region": "CN", "flag": "🇨🇳", "q": "Goldwind"},
    {"name": "Ming Yang", "region": "CN", "flag": "🇨🇳", "q": "Mingyang"},
    {"name": "Envision", "region": "CN", "flag": "🇨🇳", "q": "Envision Energy"},
    {"name": "BYD", "region": "CN", "flag": "🇨🇳", "q": "BYD"},
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
    {"name": "Fuji Electric", "region": "JP", "flag": "🇯🇵", "q": "Fuji Electric"},   # 전력반도체·인버터
    {"name": "Meidensha", "region": "JP", "flag": "🇯🇵", "q": "Meidensha"},
    # 🇪🇺 유럽
    # Siemens 계열은 구체적인 쪽을 먼저 둔다. 목록에서 앞선 항목이 공개번호 dedup 으로
    # 먼저 가져가게 하려는 것.
    # 총계는 출원인별 독립 질의라 dedup 이 듣지 않는다 → 넓은 q="Siemens" 로는 Energy·
    # Gamesa 가 그대로 합산됐다(실측: Siemens 183 · Energy 53 · Gamesa 75). 총계가
    # 겹치면 분야별 경쟁 구도의 지분이 부풀려지므로 모회사 법인명으로 좁힌다.
    # 표기가 문서마다 흔들려(AKTIENGESELLSCHAFT / AG) 둘을 OR 로 묶었다.
    {"name": "Siemens Energy", "region": "EU", "flag": "🇩🇪", "q": "Siemens Energy"},
    {"name": "Siemens Gamesa", "region": "EU", "flag": "🇪🇸", "q": "Siemens Gamesa"},
    # seq: '앞 항목보다 뒤에 와야 한다'는 표시. 주간 수집이 시작점을 회전시키므로
    # (_collect_order) 회전이 이 사이를 끊고 들어오지 못하게 막는다.
    {"name": "Siemens", "region": "EU", "flag": "🇩🇪", "seq": True,
     "q": ["Siemens Aktiengesellschaft", "Siemens AG"]},                                      # ✔
    {"name": "ABB", "region": "EU", "flag": "🇨🇭", "q": "ABB"},                                # ✔
    {"name": "Schneider Electric", "region": "EU", "flag": "🇫🇷", "q": "Schneider Electric"},  # ✔
    {"name": "Bosch", "region": "EU", "flag": "🇩🇪", "q": "Robert Bosch"},
    {"name": "Vestas", "region": "EU", "flag": "🇩🇰", "q": "Vestas Wind Systems"},
    {"name": "Nordex", "region": "EU", "flag": "🇩🇪", "q": "Nordex"},
    {"name": "Prysmian", "region": "EU", "flag": "🇮🇹", "q": "Prysmian"},     # 해저·초고압 케이블
    {"name": "Nexans", "region": "EU", "flag": "🇫🇷", "q": "Nexans"},
    {"name": "NKT", "region": "EU", "flag": "🇩🇰", "q": "NKT"},               # HVDC 케이블
    {"name": "Framatome", "region": "EU", "flag": "🇫🇷", "q": "Framatome"},
    {"name": "Rolls-Royce SMR", "region": "EU", "flag": "🇬🇧", "q": "Rolls-Royce SMR"},
    {"name": "Legrand", "region": "EU", "flag": "🇫🇷", "q": "Legrand"},
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
# ipc: KIPRISplus 국내 공보 검색에 쓸 IPC 접두(여러 개면 분야당 여러 번 조회).
#   실측(8/25): KIPRISplus 국내 항목별검색에 cpcNumber 파라미터가 **없다**
#   (cpcNumber=Y04S → INVALID_REQUEST_PARAMETER). ipcNumber 만 받는다.
#   그래서 CPC 전용 코드는 IPC 로 갈아 끼워야 한다:
#     Y04S(스마트그리드 ICT) → IPC 에 없음. 원격 감시·제어는 H02J13,
#       전력량 계측은 G01R21/G01R22 가 같은 자리를 덮는다.
#     Y02E(에너지 감축) → 애초에 우리 분야 정의에는 쓰지 않았다.
#   나머지 일곱 분야는 CPC 와 IPC 가 같은 코드라 그대로 쓴다.
CATEGORIES = [
    {"key": "nuclear", "emoji": "☢️", "name": "원전·SMR",
     "cpc": ["G21C", "G21D"], "ipc": ["G21C", "G21D"], "match": ["G21"]},
    {"key": "renew", "emoji": "🌿", "name": "재생에너지·저장",
     "cpc": ["H02S", "H01M10", "F03D"], "ipc": ["H02S", "H01M10", "F03D"],
     "match": ["H02S", "F03D", "H01M"]},
    {"key": "meter", "emoji": "🧮", "name": "계량·스마트그리드",
     "cpc": ["Y04S", "G01R21", "G01R22"],
     "ipc": ["G01R21", "G01R22", "H02J13"],     # Y04S 대체 — 위 주석 참고
     "match": ["Y04S", "G01R21", "G01R22"]},
    {"key": "datacenter", "emoji": "🖥️", "name": "데이터센터·무정전전원",
     "cpc": ["H02J9"], "ipc": ["H02J9"], "match": ["H02J9"]},
    {"key": "supply", "emoji": "⚡", "name": "전력수급·수요관리",
     "cpc": ["H02J3"], "ipc": ["H02J3"], "match": ["H02J3", "H02J13"]},
    {"key": "mega", "emoji": "🏗️", "name": "전력반도체·전력변환",
     "cpc": ["H02M", "H01L29"], "ipc": ["H02M", "H01L29"],
     "match": ["H02M", "H01L", "H03K17"]},
    {"key": "industry", "emoji": "🏭", "name": "전력설비·기기",
     "cpc": ["H01H33", "H02B"], "ipc": ["H01H33", "H02B"], "match": ["H01H", "H02B"]},
    {"key": "grid", "emoji": "🔌", "name": "송·변전·전력망",
     "cpc": ["H02G", "H01F27", "H02J1"], "ipc": ["H02G", "H01F27", "H02J1"],
     "match": ["H02G", "H01F", "H02J"]},
]

# ── KIPRISplus (국내 공보) ────────────────────────────────────────
# 명세서 실측(8/25). OPS 와 결정적으로 다른 점: **출원인 없이 분야+기간만으로**
# 조회된다. OPS 는 pa= 가 필수라 큐레이션한 65곳 안에서만 볼 수 있었는데, 여기서는
# 그 분야에 실제로 출원하는 국내 주체가 전부 나온다(대학 산학협력단·중소기업 포함)
# → 오래 막혀 있던 '공급자 축'이 목록 없이 풀린다.
KIPRIS_KEY = os.getenv("KIPRIS_KEY", "")
KIPRIS_BASE = os.getenv("KIPRIS_BASE", "http://plus.kipris.or.kr/kipo-api/kipi")
KIPRIS_SERVICE = os.getenv("KIPRIS_SERVICE", "patUtiModInfoSearchSevice")
KIPRIS_KEYPARAM = os.getenv("KIPRIS_KEYPARAM", "ServiceKey")
KIPRIS_ROWS = int(os.getenv("KIPRIS_ROWS", "100"))       # 한 요청에 받을 건수
# 분야당 상한. OPS 때와 달리 '표본'이 아니라 **모집단 전수**를 받는 것이 목표다
# (그래야 출원인 총계를 세기만 하면 되고, 표본 편향이 아예 생기지 않는다).
# 실측: H02M × 최근 90일 = 595건. 분야당 2천이면 8대 분야가 넉넉히 들어온다.
# 상한에 걸리면 수집기가 어느 분야가 잘렸는지 로그와 stats 에 남긴다.
KIPRIS_PER_CAT = int(os.getenv("KIPRIS_PER_CAT", "2000"))
KIPRIS_DELAY = float(os.getenv("KIPRIS_DELAY", "0.3"))

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
APPLICANT_BY_NAME = {a["name"]: a for a in APPLICANTS}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
