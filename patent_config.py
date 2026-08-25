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

# 서지상세가 주는 applicantCountry 는 실제 나라 코드(DE·DK·TW·CA…)다. 화면의
# 국적 축은 다섯 갈래(미국·한국·중국·일본·유럽)로 잡혀 있고, 큐레이션 별칭도
# 이미 그렇게 쓰고 있었다 — Vestas 는 국기 🇩🇰 에 지역 EU 다. 같은 규칙으로 접는다.
# 유럽 밖의 나라(대만·캐나다·인도…)는 접지 않고 그 코드 그대로 둔다. 다섯 갈래에
# 없다고 '미상'으로 만들면 아는 것을 모른다고 하는 셈이다.
EUROPE = {
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
    "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MC",
    "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "SM",
    "TR", "UA",
}


def region_of(cc: str) -> str:
    """나라 코드 → 화면의 국적 축 값. 빈 값은 빈 값 그대로(모르는 것은 모른다)."""
    cc = (cc or "").strip().upper()
    if not cc:
        return ""
    if cc in REGION_LABEL:
        return cc
    return "EU" if cc in EUROPE else cc


def flag_of(cc: str) -> str:
    """나라 코드 → 국기 이모지. 표에 없는 나라도 지역표시자로 만들어 준다.

    화면의 flg() 가 지역표시자 쌍을 받으면 우리가 그린 국기로 바꾸고, 안 그린
    나라는 두 글자 배지로 보인다 — 그러라고 만든 장치라 여기서 표를 늘리지 않는다.
    """
    cc = (cc or "").strip().upper()
    if cc in REGION_LABEL:
        return REGION_LABEL[cc][0]
    if len(cc) == 2 and cc.isalpha():
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc)
    return ""

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
# KIPRIS 는 한글 법인명을 준다('주식회사 엘지에너지솔루션', '도요타 지도샤(주)').
# 영문 별칭과 음절이 달라(엘지 ≠ LG) 정규화로는 못 붙는다 — 첫 실전 수집에서
# 같은 회사가 둘로 갈렸다(LG에너지솔루션 2264 + 주식회사 엘지에너지솔루션 502).
# 그래서 q 에 한글 표기를 함께 적는다(한국수력원자력·산일전기에 이미 쓰던 방식).
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
    {"name": "삼성전자", "region": "KR", "flag": "🇰🇷",
     "q": ["Samsung Electronics", "삼성전자"]},              # ✔
    {"name": "LG에너지솔루션", "region": "KR", "flag": "🇰🇷",
     "q": ["LG Energy Solution", "엘지에너지솔루션", "LG에너지솔루션"]},
    {"name": "삼성SDI", "region": "KR", "flag": "🇰🇷",
     "q": ["Samsung SDI", "삼성에스디아이", "삼성SDI"]},
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
    {"name": "Huawei", "region": "CN", "flag": "🇨🇳", "q": ["Huawei", "화웨이"]},                          # ✔
    {"name": "CATL", "region": "CN", "flag": "🇨🇳",
     "q": ["Contemporary Amperex Technology", "엠퍼렉스"]},   # ✔
    {"name": "CNNC", "region": "CN", "flag": "🇨🇳", "q": "China National Nuclear"},   # 중국핵공업집단
    {"name": "CGN", "region": "CN", "flag": "🇨🇳", "q": "China General Nuclear"},     # 중국광핵집단
    {"name": "Sungrow", "region": "CN", "flag": "🇨🇳", "q": "Sungrow"},               # 인버터·ESS PCS
    {"name": "TBEA", "region": "CN", "flag": "🇨🇳", "q": "TBEA"},                     # 변압기
    {"name": "Goldwind", "region": "CN", "flag": "🇨🇳", "q": "Goldwind"},
    {"name": "Ming Yang", "region": "CN", "flag": "🇨🇳", "q": "Mingyang"},
    {"name": "Envision", "region": "CN", "flag": "🇨🇳", "q": "Envision Energy"},
    {"name": "BYD", "region": "CN", "flag": "🇨🇳", "q": ["BYD", "비와이디"]},
    # 🇯🇵 일본
    {"name": "Hitachi Energy", "region": "JP", "flag": "🇯🇵", "q": "Hitachi Energy"},          # ✔
    # 히타치제작소(Hitachi, Ltd.). 그냥 "Hitachi" 로 두면 위 Hitachi Energy 까지 걸려
    # 총계가 부풀려지므로 어구로 묶어 분리한다(목록 중복은 공개번호 dedup 이 막지만
    # stats 의 총계는 출원인별 독립 질의라 겹치면 그대로 부푼다).
    {"name": "Hitachi", "region": "JP", "flag": "🇯🇵",
     "q": ["Hitachi Ltd", "日立製作所"]},
    {"name": "Mitsubishi Electric", "region": "JP", "flag": "🇯🇵",
     "q": ["Mitsubishi Electric", "미쓰비시덴키", "미쓰비시 전기",
           "三菱電機"]},
    {"name": "Toshiba", "region": "JP", "flag": "🇯🇵",
     "q": ["Toshiba", "도시바", "東芝"]},
    {"name": "Panasonic", "region": "JP", "flag": "🇯🇵",
     "q": ["Panasonic", "파나소닉", "パナソニック"]},
    {"name": "Kyocera", "region": "JP", "flag": "🇯🇵",
     "q": ["Kyocera", "京セラ"]},
    {"name": "Toyota", "region": "JP", "flag": "🇯🇵",
     "q": ["Toyota", "도요타", "トヨタ"]},
    {"name": "Sumitomo Electric", "region": "JP", "flag": "🇯🇵",
     "q": ["Sumitomo Electric", "스미토모", "住友電気"]},
    {"name": "Furukawa Electric", "region": "JP", "flag": "🇯🇵", "q": "Furukawa Electric"},
    {"name": "Fuji Electric", "region": "JP", "flag": "🇯🇵",
     "q": ["Fuji Electric", "富士電機"]},   # 전력반도체·인버터
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
    {"name": "ABB", "region": "EU", "flag": "🇨🇭", "q": ["ABB", "에이비비"]},                                # ✔
    {"name": "Schneider Electric", "region": "EU", "flag": "🇫🇷",
     "q": ["Schneider Electric", "슈나이더"]},  # ✔
    {"name": "Bosch", "region": "EU", "flag": "🇩🇪", "q": ["Robert Bosch", "보쉬"]},
    {"name": "Vestas", "region": "EU", "flag": "🇩🇰",
     "q": ["Vestas Wind Systems", "베스타스"]},
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
# IPC 접두당 상한. OPS 때와 달리 '표본'이 아니라 **모집단 전수**를 받는 것이
# 목표다 (그래야 출원인 총계를 세기만 하면 되고, 표본 편향이 아예 생기지 않는다).
# 실측: H02M × 최근 90일 = 595건. 2천이면 넉넉하다고 봤는데 실전에서 재생에너지·
# 저장이 잘렸다 — 그 분야에 배터리(H01M10)가 들어 있고, 배터리는 다른 어떤 전력
# 접두보다 출원이 많다. 잘리면 그 분야의 출원인 총계만 전수가 아니게 되어 한 표
# 안에서 성격이 다른 수치가 섞이므로, 배터리가 들어와도 남는 값으로 올린다.
# 접두 하나가 5천을 넘으면 다시 잘리고, 그때는 로그가 어느 분야인지 말해 준다.
KIPRIS_PER_CAT = int(os.getenv("KIPRIS_PER_CAT", "5000"))
KIPRIS_DELAY = float(os.getenv("KIPRIS_DELAY", "0.3"))

# ── 해외특허 (계열이 완전히 다르다 — patent_source_foreign 주석 참고) ──
# 전부 러너 실측(2026-08-25). 이름의 오타 'Advenced' 가 진짜다 — 철자를 고치면
# 경로 없음이 온다. 키 질의도 국내(ServiceKey)와 달리 accessKey 다.
FOREIGN = os.getenv("KIPRIS_FOREIGN", "on").lower() not in ("0", "off", "false")
FOREIGN_BASE = os.getenv("FOREIGN_BASE", "http://plus.kipris.or.kr/openapi/rest")
FOREIGN_SERVICE = os.getenv("FOREIGN_SERVICE",
                            "ForeignPatentAdvencedSearchService")
FOREIGN_OP = os.getenv("FOREIGN_OP", "advancedSearch")
FOREIGN_KEYPARAM = os.getenv("FOREIGN_KEYPARAM", "accessKey")
# 대상국. 쉼표로 한 번에 여러 개를 줄 수 있다(US,EP,JP,CN 실측). 비우면 필수값
# 오류(11)가 난다. KR 은 넣지 않는다 — 국내는 국내 API 로 전수를 받고 있어
# 여기서 또 받으면 같은 특허가 두 벌로 들어온다.
FOREIGN_COUNTRIES = [s for s in os.getenv(
    "FOREIGN_COUNTRIES", "US,EP,JP,CN").split(",") if s.strip()]
# 한 요청에 받을 건수. docsCount=50 이 먹는 것을 확인했다(기본은 30).
FOREIGN_ROWS = int(os.getenv("FOREIGN_ROWS", "50"))
FOREIGN_PER_CAT = int(os.getenv("FOREIGN_PER_CAT", "3000"))

# ── 해외 출원인 국적 보강 ────────────────────────────────────────
# 해외 **검색** 응답에는 출원인 국적이 없다. 그래서 큐레이션 별칭에 걸리는 곳만
# 국적이 붙고 나머지는 비어 있었다 — 실측 출원인 5,471곳 중 4,256곳이 미상이고,
# 그 대부분이 중국 대학·국유기업이다. 공개국으로 대신 채우면 'US 에 낸 일본
# 회사'가 미국 기업이 되므로 그것은 하지 않는다.
#
# **서지상세**에는 applicantCountry 가 있다(실측 2026-08-25):
#   US 공개 202600213551A1 → Panasonic … / JP
#   US 공개 202600221299A1 → Tsinghua University / CN
# 공개국과 출원인 국적이 실제로 갈리는 것까지 확인됐다.
#
# 다만 건당 1요청이고 응답이 무겁다(청구항·초록까지 실려 온다). 그래서
#   · **출원인당 1회**만 부른다. 국적은 출원인의 성질이지 문서의 성질이 아니다.
#   · 한 실행에 상한을 두고 stats.json 에 누적한다(OPS 시절 offices 와 같은 방식).
#   · 몇 갈래로 나눠 동시에 부른다. 하나가 느리다고 줄 전체가 서지는 않게.
ORIGIN = os.getenv("KIPRIS_ORIGIN", "on").lower() not in ("0", "off", "false")
ORIGIN_BASE = os.getenv("ORIGIN_BASE", "http://plus.kipris.or.kr/openapi/rest")
ORIGIN_SERVICE = os.getenv("ORIGIN_SERVICE", "ForeignPatentBibliographicService")
ORIGIN_OP = os.getenv("ORIGIN_OP", "bibliographicInfo")
ORIGIN_KEYPARAM = os.getenv("ORIGIN_KEYPARAM", "accessKey")
# 실측: 건당 약 5초. 여섯 갈래 동시면 800곳에 10분 남짓이고, 4천여 곳이 엿새면
# 다 찬다. 매일 실행에 얹기에 그 정도가 한계다.
ORIGIN_PER_RUN = int(os.getenv("ORIGIN_PER_RUN", "800"))   # 0 이면 끔
ORIGIN_WORKERS = int(os.getenv("ORIGIN_WORKERS", "6"))
ORIGIN_TIMEOUT = int(os.getenv("ORIGIN_TIMEOUT", "20"))
# 몇 번 연달아 실패하면 그만 시도한다. 번호 표기가 안 맞는 문헌이 섞여 있으면
# 매 실행 같은 것을 다시 두드리며 상한을 다 써 버린다.
ORIGIN_MAX_TRY = int(os.getenv("ORIGIN_MAX_TRY", "3"))

# ── 국내 공보 출원인 국적 ─────────────────────────────────────────
# 국내 수집기(patent_source_kipris._identify)는 별칭표에 없는 출원인을 **전부 KR**
# 로 적는다. 한국에 공개한 외국 기업이 그대로 국내 기업이 된다. 실측(8/25, 누적
# 16,265건): 이름에 외국 법인 표기가 든 것만 세어도 국내공보·KR표시 4,905건 중
# 461건(9.4%)·출원인 219곳이고, '🇰🇷 국내 N%' 배지가 분야마다 0.3~5.9%p 부풀어
# 있었다. 이름만 보고 가르는 것은 하지 않는다(포스코홀딩스는 '홀딩스' 때문에
# 외국으로 잡힌다) — 해외 쪽에서 공개국 추정을 실측으로 반박하고 버린 것과 같은 이유다.
#
# 국내 서지상세에는 그 값이 실제로 있다(8/25 실측, CATL 출원 1020267024585):
#   <applicantInfoArray><applicantInfo><country>중국</country> …
#   <agentInfoArray><agentInfo><country>대한민국</country> …   ← 대리인. 함정이다
# 대리인은 거의 언제나 한국 특허법인이라, 스코프 없이 <country> 를 읽으면 전부
# KR 로 돌아온다. 반드시 applicantInfo 안에서만 읽는다.
#
# 값이 ISO 코드가 아니라 **한국어 나라 이름**이라 표가 필요하다. 표에 없는 이름은
# 추측하지 않고 사유로 남겨 로그에 찍는다 — 그걸 보고 사람이 한 줄 더한다.
ORIGIN_KR = os.getenv("KIPRIS_ORIGIN_KR", "on").lower() not in ("0", "off", "false")
ORIGIN_KR_BASE = os.getenv("ORIGIN_KR_BASE", KIPRIS_BASE)
ORIGIN_KR_SERVICE = os.getenv("ORIGIN_KR_SERVICE", KIPRIS_SERVICE)
ORIGIN_KR_OP = os.getenv("ORIGIN_KR_OP", "getBibliographyDetailInfoSearch")
ORIGIN_KR_KEYPARAM = os.getenv("ORIGIN_KR_KEYPARAM", KIPRIS_KEYPARAM)
# 대상은 국내 출원인 1,174곳(실측)이라 해외(4천여 곳)보다 훨씬 작다 — 이틀이면 찬다.
ORIGIN_KR_PER_RUN = int(os.getenv("ORIGIN_KR_PER_RUN", "600"))   # 0 이면 끔

COUNTRY_KO = {
    "대한민국": "KR", "한국": "KR",
    "미국": "US", "중국": "CN", "일본": "JP", "대만": "TW", "홍콩": "HK",
    "독일": "DE", "프랑스": "FR", "영국": "GB", "네덜란드": "NL", "스위스": "CH",
    "스웨덴": "SE", "덴마크": "DK", "핀란드": "FI", "노르웨이": "NO",
    "이탈리아": "IT", "스페인": "ES", "오스트리아": "AT", "벨기에": "BE",
    "아일랜드": "IE", "룩셈부르크": "LU", "폴란드": "PL", "체코": "CZ",
    "헝가리": "HU", "포르투갈": "PT", "그리스": "GR", "터키": "TR",
    "튀르키예": "TR", "러시아": "RU", "우크라이나": "UA",
    "캐나다": "CA", "멕시코": "MX", "브라질": "BR", "칠레": "CL",
    "이스라엘": "IL", "인도": "IN", "싱가포르": "SG", "말레이시아": "MY",
    "태국": "TH", "베트남": "VN", "인도네시아": "ID", "필리핀": "PH",
    "호주": "AU", "오스트레일리아": "AU", "뉴질랜드": "NZ",
    "남아프리카공화국": "ZA", "사우디아라비아": "SA",
    "아랍에미리트": "AE", "아랍에미리트연합": "AE",
    # 첫 실행(600곳)에서 표에 없어 걸린 이름. 로그가 이름을 실어 올려 준 것을
    # 그대로 옮겨 적었다 — 추측한 것이 아니다.
    "세이쉘": "SC", "세이셸": "SC",
    # 아직 안 걸렸지만 특허 출원인 주소로 흔한 곳들(조세피난처 법인·유럽 소국·
    # 신흥국). 미리 채워 두면 로그를 한 번 덜 오간다.
    "케이맨제도": "KY", "버진아일랜드": "VG", "버뮤다": "BM", "파나마": "PA",
    "리히텐슈타인": "LI", "모나코": "MC", "몰타": "MT", "키프로스": "CY",
    "아이슬란드": "IS", "슬로베니아": "SI", "슬로바키아": "SK",
    "루마니아": "RO", "불가리아": "BG", "크로아티아": "HR", "세르비아": "RS",
    "에스토니아": "EE", "라트비아": "LV", "리투아니아": "LT", "벨라루스": "BY",
    "카자흐스탄": "KZ", "우즈베키스탄": "UZ", "몽골": "MN", "마카오": "MO",
    "아르헨티나": "AR", "콜롬비아": "CO", "페루": "PE", "우루과이": "UY",
    "이집트": "EG", "나이지리아": "NG", "케냐": "KE", "모로코": "MA",
    "카타르": "QA", "쿠웨이트": "KW", "이란": "IR", "요르단": "JO",
    "파키스탄": "PK", "방글라데시": "BD", "스리랑카": "LK", "네팔": "NP",
    "미얀마": "MM", "캄보디아": "KH", "라오스": "LA", "브루나이": "BN",
}

# ── CPC 보강 ─────────────────────────────────────────────────────
# 검색은 IPC 로만 되지만(cpcNumber 파라미터 없음), **출원번호를 주면 그 특허의
# CPC 를 받을 수 있다**(명세서 실측):
#   http://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice/patentCpcInfo
#     ?applicationNumber=1020060118886&accessKey=…
# 같은 서비스가 /openapi/rest 에도 있고 거기서는 키 이름이 accessKey 다.
#
# 이걸로 Y04S 처럼 CPC 에만 있는 코드를 되찾아 8대 분야 분류를 원래 정의대로
# 맞출 수 있다. 다만 건당 1요청이라 전수 보강은 비싸다 → 상한을 두고, 분류가
# 바뀔 여지가 큰 것부터 채운다(무엇을 몇 건 보강했는지는 로그에 남긴다).
KIPRIS_CPC_BASE = os.getenv("KIPRIS_CPC_BASE",
                            "http://plus.kipris.or.kr/openapi/rest")
KIPRIS_CPC_KEYPARAM = os.getenv("KIPRIS_CPC_KEYPARAM", "accessKey")
KIPRIS_CPC_LIMIT = int(os.getenv("KIPRIS_CPC_LIMIT", "400"))   # 0 이면 끔

CATEGORY_BY_KEY = {c["key"]: c for c in CATEGORIES}
APPLICANT_BY_NAME = {a["name"]: a for a in APPLICANTS}


def is_mock() -> bool:
    return MOCK_MODE == "on"


def force_live() -> bool:
    return MOCK_MODE == "off"
