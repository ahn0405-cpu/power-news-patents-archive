"""KIPRISplus 탐색 — 발급받은 키로 **무엇이 열리는지**를 실측한다.

왜 프로브부터인가: KIPRISplus 는 서비스 단위로 신청·승인된다. 키 하나가 모든
서비스를 여는 게 아니라 승인된 서비스만 열린다. 어느 것이 승인됐는지 모르는 채
수집기를 짜면 매주 실패를 기다리게 된다(OPS 로 이미 겪었다).

개발 환경에서는 plus.kipris.or.kr 이 세션 프록시 허용목록에 없어 막힌다(실측:
`Host not in allowlist`). 러너는 외부 인터넷이 열려 있으므로 거기서 돌린다.

이 프로브가 답해야 할 세 가지 — 셋을 구분하지 못하면 진단이 안 된다:
  ① 러너에서 KIPRIS 서버에 닿는가            → 연결 실패 / 타임아웃
  ② 엔드포인트 **경로**가 맞는가              → 404 · 포털 HTML
  ③ 그 서비스가 이 키로 **승인**돼 있는가     → resultCode 인증·권한 오류

②의 함정(1차 실행에서 실제로 걸렸다): KIPRIS 는 없는 경로에 **HTTP 200 으로
한국어 404 안내 HTML** 을 돌려준다. 코드만 보고 판정하면 '열림'으로 읽혀 다섯
서비스가 열린 것처럼 나온다 — 전부 거짓이었다. 그래서 본문이 XML/JSON 인지까지
확인해야 판정이 선다.

사용(러너에서): KIPRIS_KEY=... python probe_kipris.py
  선택 입력(환경변수):
    KIPRIS_BASE      기준 경로 고정(비우면 후보를 훑어 사는 것을 찾는다)
    KIPRIS_SERVICE   특허·실용 서비스 이름 고정
    KIPRIS_PATHS     "서비스/오퍼레이션[?추가질의] ..." 공백 구분 — 직접 지정할 때
    KIPRIS_KEYPARAM  키 질의 이름 고정(기본: 후보를 차례로 시도)

키는 절대 파일에 적지 않는다. GitHub Secret → 환경변수로만 받는다.
"""
from __future__ import annotations

import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = os.getenv("KIPRIS_KEY", "").strip()
TIMEOUT = 25
# 응답 앞부분만 찍는다(로그가 길면 읽기 어렵다). 다만 해외 응답은 한 건에 필드가
# 스무 개 넘어 900자로는 목록 한 건도 다 못 본다 — 필요할 때 KIPRIS_HEAD 로 늘린다.
HEAD = int((os.getenv("KIPRIS_HEAD") or "").strip() or 900)   # 빈 입력=기본값
# 태그 이름 정규식. 찍으면 그 이름에 걸리는 태그 블록만 따로 뽑아 앞에 놓는다.
# '앞부분 N자'는 찾는 필드가 뒤에 있으면 쓸모가 없다(실측: 국내 서지상세는 청구항이
# 앞을 다 먹어 출원인 블록이 4천 자 밖으로 밀렸다).
PICK = (os.getenv("KIPRIS_PICK") or "").strip()

# 명세서 두 건에서 확인된 요청 주소(추측 아님):
#   국내 특허·실용 공개·등록공보
#     http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch
#       ?word=센서&year=0&ServiceKey=…
#   해외특허
#     http://plus.kipris.or.kr/openapi/rest/ForeignPatentGeneralSearchService/wordSearch
#
# 두 계열이 실제로 공존한다 — 국내는 kipo-api/kipi, 해외는 openapi/rest 다.
# 앞서 '/openapi/rest 계열은 죽었다'고 판단한 것은 틀렸다. 없는 서비스 이름에
# 포털 HTML 이 오는 바람에 경로 전체가 죽은 것처럼 보였을 뿐이다.
BASES = [
    "http://plus.kipris.or.kr/kipo-api/kipi",
    "http://plus.kipris.or.kr/openapi/rest",
    "https://plus.kipris.or.kr/kipo-api/kipi",
    "https://plus.kipris.or.kr/openapi/rest",
]
# (기록) 여기 넣었던 이름은 전부 틀렸다. 진짜는 patUtiMod**InfoSearchSevice** —
# 내가 쓴 patUtiMod**el**InfoSearchSevice 와 'el' 두 글자 차이다. 그 두 글자 때문에
# 열 번 넘게 두드린 것이 전부 막혔고, 심지어 kipo-api 게이트웨이는 없는 서비스에도
# resultCode 31 을 돌려줘서 '승인/기간 문제'로 오래 헤맸다. 이름은 문서에서 가져온다.
SERVICES = [
    "patUtiModInfoSearchSevice",
]
# 키 질의 이름이 서비스 세대마다 다르다(accessKey / ServiceKey / serviceKey).
# 이름이 틀리면 '인증 실패'로 돌아와 승인 안 된 서비스와 구분되지 않는다 → 전부 시험.
# 3차 실측: 같은 요청에 이름만 바꿔 보니 ServiceKey 만 파라미터 검사를 통과했다
# (나머지는 '필수값 없음' 취급). 그래서 이 이름을 먼저 시험한다.
KEY_PARAMS = ["ServiceKey", "accessKey", "serviceKey"]

# resultCode 를 사람 말로. 코드만 남기면 로그를 봐도 다음 수가 안 정해진다.
CODE_MEANS = {
    "10": "요청 파라미터 오류 — 필수값이 없거나 이름이 틀렸다",
    # 문구는 '만료'지만 실측 정황은 더 넓다: 존재하지도 않는 서비스 이름에도 31 이
    # 온다. 활용기간은 **서비스마다** 부여되므로, 신청하지 않은 서비스는 기간이
    # 아예 없어 같은 코드로 떨어지는 것으로 보인다 → '기간 만료'로 단정하면
    # 고칠 곳(연장 신청 vs 서비스 추가 신청)을 잘못 잡는다.
    "31": "이 키에 이 서비스의 활용기간이 없다 — 만료됐거나, 애초에 신청 안 된 서비스",
    "20": "서비스 미신청 — 이 서비스가 승인 목록에 없다",
    "22": "요청 한도 초과",
    "30": "등록되지 않은 키",
}

# 기준 호출은 명세서의 샘플 그대로 — 국내 공보(우리 주력 수집원, 갱신 일 단위).
DISCOVERY_PATH = "patUtiModInfoSearchSevice/getWordSearch"
DISCOVERY_PARAMS = {"word": "센서", "year": "0"}

# [문서] = 명세서에서 그대로 옮긴 것 · [추측] = 아직 근거 없는 것.
# 섞어 두면 실패했을 때 '없는 이름이라 그런지, 승인이 없어 그런지'를 또 헷갈린다.
_KR = "patUtiModInfoSearchSevice"          # 국내 공보 (kipo-api/kipi)
_FG = "ForeignPatentGeneralSearchService"   # 해외 일반검색 (openapi/rest)
# 명세서 표기가 Adv**e**nced 다 — 오타지만 그게 실제 이름이다(Service→Sevice 와
# 같은 계열). 눈으로 읽으면 무의식적으로 교정하게 되므로 확대해 확인했다.
_FGA = "ForeignPatentAdvencedSearchService" # 해외 항목별검색 — 분류·출원인·기간
# 항목은 (설명, 경로, 질의) 또는 (설명, 경로, 질의, 키질의이름).
# 키 질의 이름이 계열마다 다르다 — kipo-api 는 ServiceKey 로 열렸고, openapi/rest
# 는 같은 키를 ServiceKey 로 보내자 'Invalid AccessKey Error'(30) 를 돌려줬다.
PROBES = [
    # ── 살아 있는지 확인 (회귀 감시) ────────────────────────────────
    ("[문서] 국내 공보 단어검색 (샘플 그대로)", f"{_KR}/getWordSearch",
     {"word": "센서", "year": "0"}),

    # ── 수집 설계의 갈림길 — 항목별검색으로 무엇이 되는가 ───────────
    # 지금 OPS 가 하는 일은 '분야(CPC) × 출원인 × 최근 기간'이다. 그 세 축이
    # 이 API 로 옮겨지는지가 설계를 가르므로 축을 하나씩 떼어 확인한다.
    ("[실측] 분류로 찾기 — IPC (H02M 전력변환)", f"{_KR}/getAdvancedSearch",
     {"ipcNumber": "H02M", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    # Y04S(스마트그리드)·Y02E 는 CPC 에만 있고 IPC 에는 없다. cpcNumber 가 먹으면
    # 우리 8대 분야 분류를 그대로 옮길 수 있고, 안 먹으면 분류 체계를 손봐야 한다.
    ("[실측] 분류로 찾기 — CPC 파라미터가 있나 (Y04S)", f"{_KR}/getAdvancedSearch",
     {"cpcNumber": "Y04S", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("[실측] 출원인으로 찾기 (한국전력공사)", f"{_KR}/getAdvancedSearch",
     {"applicant": "한국전력공사", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    # 최근 N 일치만 받아야 주간 수집이 가볍다. 날짜 범위 표기를 확인한다.
    ("[실측] 기간으로 자르기 (공개일 20260101~)", f"{_KR}/getAdvancedSearch",
     {"ipcNumber": "H02M", "openDate": "20260101~20260825",
      "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("[실측] 분야 × 출원인 (한전 × H02J)", f"{_KR}/getAdvancedSearch",
     {"applicant": "한국전력공사", "ipcNumber": "H02J",
      "patent": "true", "numOfRows": "3", "pageNo": "1"}),

    # ── 해외특허 — 계열이 달라 키 질의 이름도 다르다 ────────────────
    ("[문서] 해외특허 단어검색 · 미국 (키질의=accessKey)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FG}/wordSearch",
     {"searchWord": "power converter", "searchWordRange": "10",
      "currentPage": "1", "collectionValues": "US"}, "accessKey"),

    # ── 해외 항목별검색 — 명세서에서 확인 (추측 아님) ───────────────
    # 단어검색은 검색어 하나만 받아 분야 매트릭스를 못 세운다. 분류로 조회하는
    # 것은 별도 **서비스**다(오퍼레이션이 아니라):
    #   http://plus.kipris.or.kr/openapi/rest/ForeignPatentAdvancedSearchService/
    #   오퍼레이션 advancedSearch · 입력 ipc · applicant · openDate ·
    #   collectionValues(US·EP·PCT…) · free · inventionName · abstracts …
    # 국내는 ipcNumber, 해외는 ipc 로 이름이 다르다 — 섞으면 조용히 빈 결과가 된다.
    ("[문서] 해외 항목별검색 — IPC (H02M · 미국)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    ("[문서] 해외 항목별검색 — IPC × 기간 (공개일 2026)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260101~20260825",
      "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    ("[문서] 해외 항목별검색 — 출원인 (Siemens · 유럽)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"applicant": "Siemens", "collectionValues": "EP", "currentPage": "1"},
     "accessKey"),
    ("[대조] 해외 항목별검색 — 철자를 고친 이름(Advanced)",
     "http://plus.kipris.or.kr/openapi/rest/"
     "ForeignPatentAdvancedSearchService/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    # 해외 서지정보: 문헌번호+국가코드로 CPC·패밀리·청구항·인용까지 받는다.
    # 국내 CPC 보강(patentCpcInfo)에 대응하는 자리다.
    ("[문서] 해외 서지상세 (문헌번호+국가코드)",
     "http://plus.kipris.or.kr/openapi/rest/"
     "ForeignPatentBibliographicService/bibliographicInfo",
     {"literatureNumber": "10539396", "countryCode": "US"}, "accessKey"),

    # ── 2차: 해외가 열린 건 확인됐다. 이제 수집기를 짜는 데 필요한 것을 잰다 ──
    # 1차에서 드러난 것: 해외 응답은 성공인데도 resultCode 가 **비어 있고**,
    # 항목 태그가 <item> 이 아니라 <searchResult> 다. 국내와 같은 코드로는 못 읽는다.
    #
    # (가) 성공/실패를 무엇으로 가르나 — resultCode 가 비면 판정 근거가 없다.
    ("[실측] 해외 · 일부러 틀린 키 (실패는 어떻게 생겼나)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "1",
      "__badkey__": "1"}, "accessKey"),
    ("[실측] 해외 · 없는 파라미터를 넣으면",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "1",
      "nosuchparam": "1"}, "accessKey"),
    # (나) openDate 가 실제로 거르나 — 1차에서는 범위를 넣으나 빼나 첫 건이 같아
    #      걸러진 것인지 무시된 것인지 갈리지 않았다. 결과가 있을 수 없는 범위를
    #      넣어 본다. 그래도 결과가 나오면 그 파라미터는 무시되는 것이다.
    ("[실측] 해외 · 결과가 있을 수 없는 기간 (1900년)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "19000101~19000102",
      "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    ("[실측] 해외 · 좁은 기간 (2026년 7월 한 달)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    # (다) 전수 수집의 전제 — 총 건수를 주나? 한 번에 몇 건을 주나?
    ("[실측] 해외 · 한 쪽에 몇 건을 주나 (numOfRows 시도)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "1",
      "numOfRows": "5"}, "accessKey"),
    ("[실측] 해외 · 2쪽은 다른 내용인가",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US", "currentPage": "2"}, "accessKey"),
    # (라) 대상국을 여러 개 줄 수 있나 (한 번에 US+EP+JP+CN 이면 요청이 4분의 1)
    ("[실측] 해외 · 대상국 여러 개 (US,EP,JP,CN)",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "collectionValues": "US,EP,JP,CN",
      "currentPage": "1"}, "accessKey"),
    ("[실측] 해외 · 대상국을 비우면",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "currentPage": "1"}, "accessKey"),
    # (마) CPC — 국내는 검색 파라미터가 없어 출원번호로 되받아 보강했다.
    #      해외 응답에 epc 칸이 있는데 1차 표본에서는 비어 있었다. 별도 CPC
    #      파라미터가 있는지, 서지상세에 CPC 가 실려 오는지 확인한다.
    ("[실측] 해외 · cpc 파라미터가 있나",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"cpc": "Y04S", "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    ("[실측] 해외 서지상세 · 1차에서 받은 실제 문헌번호로",
     "http://plus.kipris.or.kr/openapi/rest/"
     "ForeignPatentBibliographicService/bibliographicInfo",
     {"literatureNumber": "202600213551A1", "countryCode": "US"}, "accessKey"),
    ("[실측] 해외 서지상세 · 공개번호 표기로",
     "http://plus.kipris.or.kr/openapi/rest/"
     "ForeignPatentBibliographicService/bibliographicInfo",
     {"literatureNumber": "20260213551", "countryCode": "US"}, "accessKey"),

    # ── 3차: currentPage 가 '쪽'인가 '몇 번째 건부터'인가 ────────────────
    # 2차에서 1쪽의 2번째 건이 2쪽의 1번째로 나왔다. 그대로 읽으면 currentPage 는
    # 쪽 번호가 아니라 시작 위치(1부터)다. 잘못 읽으면 같은 건을 반복해 받거나
    # 사이를 건너뛴다 — 전수 수집에서 둘 다 치명적이다. 세 쪽을 나란히 본다.
    ("[실측] 해외 쪽넘김 · currentPage=1",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "1"}, "accessKey"),
    ("[실측] 해외 쪽넘김 · currentPage=2",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "2"}, "accessKey"),
    ("[실측] 해외 쪽넘김 · currentPage=3",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "3"}, "accessKey"),
    # 한 쪽 건수를 늘릴 수 있나 — 못 늘리면 요청 수가 그대로 건수가 된다.
    ("[실측] 해외 · docsCount 로 쪽 크기 조절",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "1",
      "docsCount": "50"}, "accessKey"),
    ("[실측] 해외 · rows 로 쪽 크기 조절",
     f"http://plus.kipris.or.kr/openapi/rest/{_FGA}/advancedSearch",
     {"ipc": "H02M", "openDate": "20260701~20260731",
      "collectionValues": "US", "currentPage": "1",
      "rows": "50"}, "accessKey"),
]


def _fetch(url: str) -> tuple[int, str, str]:
    """(HTTP 코드, 본문, 오류설명). 코드 0 은 연결 자체가 안 된 것."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "ip-power-probe/1.0",
        "Accept": "application/xml, application/json;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            return r.getcode(), r.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, ""
    except Exception as e:
        return 0, "", f"{type(e).__name__}: {e}"


def _tag(body: str, name: str) -> str:
    m = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", body, re.S | re.I)
    return (m.group(1) if m else "").strip()


def _squash(body: str, n: int) -> str:
    return re.sub(r"\s+", " ", body).strip()[:n]


def _is_html(body: str) -> bool:
    """KIPRIS 는 없는 경로에 HTTP 200 + 한국어 안내 HTML 을 돌려준다(실측).
    코드만 보고 '열림'으로 찍으면 정반대 결론이 난다 → 본문으로 가른다."""
    head = body[:300].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html") \
        or "페이지를 찾을 수 없습니다" in body[:2000]


def _verdict(code: int, body: str, err: str) -> tuple[str, str]:
    """(판정, 근거). ①연결 ②경로 ③승인 을 갈라 준다."""
    if code == 0:
        return "연결안됨", err
    # 개발 환경의 세션 프록시는 허용목록 밖 호스트에 403 을 돌려준다. 그대로 두면
    # '승인 안 된 서비스'로 읽혀 정반대의 결론이 난다 — KIPRIS 의 403 과 구분한다.
    if "not in allowlist" in body or "egress settings" in body:
        return "연결안됨", f"프록시가 막음(러너에서 돌려야 함) · {_squash(body, 90)}"
    if _is_html(body):
        return "경로없음", f"HTTP {code} · KIPRIS 포털 안내 HTML (그 경로가 없다)"
    rc, msg = _tag(body, "resultCode"), _tag(body, "resultMsg")
    up = body.upper()
    if code == 404 or "SERVICE ERROR" in up or "NO_OPENAPI_SERVICE" in up:
        return "경로없음", f"HTTP {code} · {msg or _squash(body, 100)}"
    # 파라미터 오류는 승인 문제가 아니다 — 서버가 우리 요청을 받아 해석까지 했다는
    # 뜻이라 오히려 '경로가 살아 있다'는 증거다. 2차 실행에서 이 둘을 뭉뚱그려
    # 열 개 전부 '미승인'으로 찍혔는데, 실제로는 질의 이름이 틀린 것이었다.
    means = CODE_MEANS.get(rc, "")
    detail = f"HTTP {code} · resultCode={rc or '-'} {msg or _squash(body, 100)}" \
             + (f"  ({means})" if means else "")
    if "INVALID_REQUEST_PARAMETER" in up or rc == "10":
        return "요청오류", detail
    # 기간 만료는 '키를 못 읽었다'가 아니라 '키를 읽었고 지금은 못 쓴다'는 뜻이다.
    # 미승인과 섞으면 고칠 곳(코드냐, 포털 신청이냐)이 정반대로 잡힌다.
    if "DEADLINE_HAS_EXPIRED" in up or rc == "31":
        return "기간만료", detail
    if code in (401, 403) or "SERVICE_KEY" in up or "SERVICE ACCESS DENIED" in up \
       or "미신청" in body or "권한" in body or (rc and rc not in ("00", "0")):
        return "미승인/키오류", detail
    if code != 200:
        return "기타오류", f"HTTP {code} · {_squash(body, 100)}"
    return "열림", f"resultCode={rc or '-'} {msg or ''}".strip()


def _count(body: str) -> str:
    for t in ("totalCount", "count", "numOfRows"):
        v = _tag(body, t)
        if v:
            return f"{t}={v}"
    n = len(re.findall(r"<item[\s>]", body))
    return f"item={n}건" if n else "item 없음"


def _url(base: str, path: str, params: dict, kp: str) -> str:
    """path 가 절대 URL 이면 base 를 무시한다.

    국내 공보는 kipo-api/kipi, 해외특허는 openapi/rest 에 있다(명세서 실측).
    한 실행에서 둘 다 두드려야 하므로 항목마다 다른 기준 경로를 쓸 수 있어야 한다.
    """
    q = dict(params)
    # __badkey__ 는 '일부러 틀린 키로 불러 실패가 어떻게 생겼는지 본다'는 표시다.
    # 해외 응답은 성공해도 resultCode 가 비어 있어서, 성공만 봐서는 판정 근거를
    # 만들 수 없다 — 실패한 모습을 봐야 둘을 가르는 규칙이 나온다.
    bad = q.pop("__badkey__", None)
    q[kp] = "NOT-A-REAL-KEY" if bad else (KEY or "NO-KEY-PROBE")
    head = path if path.startswith("http") else f"{base}/{path}"
    return head + "?" + urllib.parse.urlencode(q)


def _discover() -> tuple[str, str, str] | None:
    """(기준 경로, 서비스 이름, 키 질의 이름). 사는 조합을 실측으로 찾는다.

    기준 호출은 이제 추측이 아니라 **명세서에 적힌 그대로**다(해외특허 1/3쪽).
    서비스 이름을 지어내던 때는 없는 이름마다 포털 HTML 이 와서 경로 전체가 죽은
    것처럼 보였다 — 그 오판을 반복하지 않으려면 기준은 문서에서 와야 한다.
    남은 미지수는 '어느 스킴이냐'와 '키 질의 이름이 무엇이냐' 둘뿐이다.
    """
    path = (os.getenv("KIPRIS_SERVICE") or "").strip() or DISCOVERY_PATH
    params = DISCOVERY_PARAMS
    fixed_base = (os.getenv("KIPRIS_BASE") or "").strip()
    fixed_kp = (os.getenv("KIPRIS_KEYPARAM") or "").strip()
    bases = [fixed_base] if fixed_base else BASES
    kps = [fixed_kp] if fixed_kp else KEY_PARAMS
    fallback = None
    for base in bases:
        for kp in kps:
            code, body, err = _fetch(_url(base, path, params, kp))
            verdict, why = _verdict(code, body, err)
            print(f"  [{verdict:9s}] {base}/{path}  (키질의={kp})")
            if verdict not in ("경로없음", "연결안됨"):
                print(f"      {why}")
            if verdict == "열림":
                return base, path.split("/")[0], kp
            # 서버가 우리 요청을 해석했다는 응답이면 '경로는 맞다'는 증거다.
            # 기간만료·미승인은 서버가 **키를 읽었다**는 뜻이라 가장 앞선 신호다.
            # 요청오류는 키를 읽기도 전에 걸린 것일 수 있다(3차 실측이 그랬다).
            rank = {"기간만료": 3, "미승인/키오류": 2, "요청오류": 1}.get(verdict, 0)
            if rank and (fallback is None or rank > fallback[0]):
                fallback = (rank, base, path.split("/")[0], kp)
    return fallback[1:] if fallback else None


def _paths(svc: str) -> list[tuple[str, str, dict]]:
    raw = (os.getenv("KIPRIS_PATHS") or "").split()
    if raw:
        out = []
        for spec in raw:
            path, _, qs = spec.partition("?")
            out.append(("(직접 지정)", path, dict(urllib.parse.parse_qsl(qs))))
        return out
    return list(PROBES)


def _shape(k: str) -> str:
    """키의 '생김새'만 요약한다. 값은 절대 찍지 않는다 — 시크릿이다.

    이중 인코딩·복사 오류는 생김새로 드러난다: base64 키에 '%' 가 섞여 있으면
    퍼센트 인코딩된 문자열을 그대로 넣은 것이고, 공백이 있으면 붙여넣기 사고다.

    '=' 는 위치까지 본다(문자는 안 찍고 자리만). base64 패딩은 끝에만 오므로,
    중간에 '=' 가 있으면 'ServiceKey=…' 같은 걸 통째로 붙여넣은 사고다.
    실측 1회차: 44자에 '=' 가 셋 — 패딩치고는 많아 자리를 확인할 값어치가 있다.
    """
    marks = {c: k.count(c) for c in "=+/-_%" if c in k}
    extra = " ".join(f"'{c}'×{n}" for c, n in marks.items()) or "특수문자 없음"
    ws = sum(c.isspace() for c in k)
    eq = [i for i, c in enumerate(k) if c == "="]
    where = f" · '=' 자리 {eq} (끝은 {len(k) - 1})" if eq else ""
    return (f"{len(k)}자 · 영문 {sum(c.isalpha() for c in k)} "
            f"숫자 {sum(c.isdigit() for c in k)} · {extra}{where}"
            + (f" · ⚠️ 공백 {ws}자 포함" if ws else ""))


def _tail_check() -> None:
    """포털에 보이는 키 꼬리와 시크릿이 같은 키인지 확인한다.

    시크릿 값을 로그에 찍으면 안 되고(러너 마스킹은 '전체 문자열'에만 걸려
    일부만 찍으면 그대로 새어 나간다), 그렇다고 대조를 안 하면 '포털에서 본 것과
    시크릿이 같은 키인가'를 영원히 확인할 수 없다. 비교를 러너 안에서 시키고
    결과(일치/불일치)만 받는다.
    """
    want = (os.getenv("KIPRIS_KEY_TAIL") or "").strip()
    if not want:
        return
    print(f"\n   포털 키와 대조 — 끝 {len(want)}자 (값은 찍지 않는다)")
    for label, val in (("있는 그대로", KEY),
                       ("공백 제거", KEY.strip()),
                       ("퍼센트 디코딩", urllib.parse.unquote(KEY))):
        ok = val.endswith(want)
        print(f"     · {label:12s} {'✅ 일치' if ok else '❌ 불일치'}")
    if not KEY.endswith(want):
        print("     → 시크릿에 든 키가 포털에서 보신 그 키가 아닙니다. "
              "다른 서비스의 키이거나, 붙여넣을 때 잘린 것입니다.")


def _key_diagnosis(base: str, svc: str, kp: str) -> None:
    """'기간 만료'가 정말 기간 문제인지 가른다.

    왜 필요한가: 사용자 포털에는 승인 완료·종료일 26.12.31 로 떠 있는데 서버는
    DEADLINE_HAS_EXPIRED 를 돌려준다. 앞뒤가 맞지 않는다. resultCode 31 을
    '기간 만료'로 읽은 것은 resultMsg 문자열에서 온 해석일 뿐, KIPRIS 가 그
    코드를 다른 상황에도 쓰는지는 확인한 적이 없다.

    가르는 방법: **일부러 틀린 키**를 같은 요청에 넣어 본다.
      · 틀린 키도 31 → 31 은 '이 키로는 못 쓴다' 는 뭉뚱그린 코드다. 우리 키가
        서버에 등록된 키로 인식되고 있다는 근거가 사라진다(키 자체를 의심).
      · 틀린 키는 다른 코드 → 우리 키는 등록된 키가 맞다. 그러면 남는 설명은
        정말로 기간(특히 **시작일 미도래**)이다.
    함께 볼 것: 퍼센트 인코딩된 키를 그대로 넣었다면 우리가 한 번 더 인코딩해
    다른 문자열이 되어 나간다 → unquote 한 값도 같이 시험한다.
    """
    dpath, dparams = DISCOVERY_PATH, DISCOVERY_PARAMS
    print("\n   키 진단 — '기간 문제'인지 '키 문제'인지 가른다")
    print(f"     키 생김새: {_shape(KEY)}")
    _tail_check()

    variants = [("있는 그대로", KEY), ("일부러 틀린 키", KEY + "ZZ")]
    un = urllib.parse.unquote(KEY)
    if un != KEY:
        variants.append(("퍼센트 디코딩", un))
        print("     ⚠️ 퍼센트 인코딩된 키로 보입니다 — 디코딩본도 함께 시험합니다.")
    # 키를 URL 에 그대로 붙이는 경로도 본다. urlencode 는 '+' 를 %2B 로 바꾸는데,
    # 서버가 그걸 되돌리지 않으면 다른 키가 되어 버린다(base64 키의 흔한 함정).
    raw_url = (f"{base}/{dpath}?" + urllib.parse.urlencode(dparams)
               + f"&{kp}={KEY}")

    for label, val in variants:
        q = dict(dparams)
        q[kp] = val
        code, body, err = _fetch(f"{base}/{dpath}?" + urllib.parse.urlencode(q))
        v, why = _verdict(code, body, err)
        print(f"     · {label:14s} [{v}] {why}")
    code, body, err = _fetch(raw_url)
    v, why = _verdict(code, body, err)
    print(f"     · {'인코딩 안 함':14s} [{v}] {why}")

    print("     읽는 법: '일부러 틀린 키'가 위와 **같은 코드**면 → 키 쪽을 의심해야 "
          "한다. **다른 코드**면 → 우리 키는 등록된 키이고, 남는 설명은 활용 "
          "시작일이 아직 오지 않았을 가능성이다.")


def _fetch_doc(url: str) -> int:
    """명세 페이지를 러너로 받아 본문만 찍는다.

    개발 환경은 plus.kipris.or.kr 이 프록시 허용목록 밖이라 명세를 열 수 없다.
    러너는 열린다 — 사람이 화면으로 읽는 대신 러너에 받아오게 해서, 요청 URL·
    오퍼레이션·파라미터 이름을 추측이 아니라 문서에서 가져온다.
    """
    print(f"명세 페이지 받아오기\n{'=' * 66}\n  {url}")
    code, body, err = _fetch(url)
    print(f"  HTTP {code}" + (f" · {err}" if err else "") + f" · {len(body)}자")
    if not body:
        return 1
    # 화면 꾸밈은 걷어내고 글자만 남긴다(스크립트·스타일은 통째로 버린다).
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    txt = re.sub(r"(?s)<[^>]+>", "\n", txt)
    txt = re.sub(r"&nbsp;?", " ", txt)
    lines = [ln.strip() for ln in txt.splitlines()]
    out, prev = [], ""
    for ln in lines:
        if ln and ln != prev:          # 빈 줄·연속 중복은 접는다
            out.append(ln)
            prev = ln
    # 본문을 통째로 찍으면 정작 필요한 세 줄(요청 URL·오퍼레이션·인증키 이름)이
    # 필드 목록 수백 줄에 묻힌다 → 먼저 뽑아서 앞에 놓는다.
    print("── 뽑은 것 " + "-" * 53)
    urls = sorted(set(re.findall(r"https?://[^\s\"'<>()]+", body)))
    api = [u for u in urls if re.search(r"openapi|kipo-api|/api/|Service|Sevice", u)]
    print(f"  API 로 보이는 주소 {len(api)}개 (전체 {len(urls)}개 중)")
    for u in api[:40]:
        print(f"    {u[:160]}")
    # 이 페이지에 요청 URL 이 없을 수 있다(해외특허 명세가 그랬다). 그럴 때 다음
    # 단서는 '명세서·설명서·다운로드' 링크다 → 앵커를 주소와 글자로 짝지어 찍는다.
    anchors = re.findall(r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                         body, re.S | re.I)
    want = re.compile(r"명세|설명서|다운로드|가이드|매뉴얼|샘플|API")
    picked, seen_a = [], set()
    for href, label in anchors:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", label)).strip()
        if not want.search(text) or (href, text) in seen_a:
            continue
        seen_a.add((href, text))
        picked.append((urllib.parse.urljoin(url, href), text))
    print(f"  '명세서·설명서·다운로드' 링크 {len(picked)}개")
    for href, text in picked[:25]:
        print(f"    {text[:40]:42s} {href[:150]}")

    keys = sorted(set(re.findall(r"\b(?:ServiceKey|serviceKey|accessKey|AccessKey)\b", body)))
    print(f"  인증키 파라미터 표기: {keys or '문서에 안 보임'}")
    ops = sorted(set(re.findall(r"\b(get[A-Z][A-Za-z]{4,40})\b", body)))
    print(f"  오퍼레이션 후보 {len(ops)}개: {', '.join(ops[:30]) or '없음'}")
    # 명세 표에 자주 나오는 파라미터 이름들(영문 소문자로 시작하는 camelCase).
    hits = [ln for ln in out if re.search(r"(요청|Request|샘플|Sample|예시|URL)", ln)]
    print(f"  '요청/URL/샘플' 이 든 줄 {len(hits)}개")
    for ln in hits[:30]:
        print(f"    {ln[:160]}")

    print("── 본문 " + "-" * 56)
    head = int(os.getenv("KIPRIS_DOC_LINES") or "180")
    print("\n".join(out[:head]))
    if len(out) > head:
        print(f"… ({len(out) - head}줄 더 있음 — KIPRIS_DOC_LINES 로 늘릴 수 있다)")
    return 0


def _check_links(urls: list[str]) -> int:
    """바깥 링크가 실제로 열리는지 한 줄씩 확인한다(카드의 '원문 보기' 검증용).

    개발 환경은 patents.google.com 도 프록시 정책에 막혀 있어(게이트웨이 403)
    링크 형식을 여기서 맞출 수 없다. 러너는 열린다 → 후보 주소를 한꺼번에 열어
    보고 제목이 실려 오는지로 판정한다. 없는 문서도 HTTP 200 을 주는 사이트가
    있으므로 코드만 보지 않고 **제목**을 본다(KIPRIS 에서 한 번 당한 방식이다).
    """
    print(f"바깥 링크 확인 — {len(urls)}개\n" + "=" * 66)
    bad = 0
    for u in urls:
        code, body, err = _fetch(u)
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body or "")
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        ok = code == 200 and title and "404" not in title
        print(f"  [{'열림' if ok else '안 열림'}] HTTP {code} · {len(body or '')}자 · {u}")
        print(f"      제목: {title[:120] or '(없음)'}" + (f" · {err}" if err else ""))
        if not ok:
            bad += 1
    print(f"\n요약: {len(urls) - bad}개 열림 / {len(urls)}개")
    return 0


def main() -> int:
    links = (os.getenv("PROBE_LINKS") or "").split()
    if links:
        return _check_links(links)
    doc = (os.getenv("KIPRIS_DOC_URL") or "").strip()
    if doc:
        return _fetch_doc(doc)

    print("KIPRISplus 프로브 — 무엇이 열리는지 실측\n" + "=" * 66)
    keyless = not KEY
    print(f"키 길이: {len(KEY)}자" + ("  ← 0자면 시크릿이 전달되지 않은 것" if keyless else ""))
    if keyless:
        # 키가 없어도 ①연결 과 ②경로 는 답할 수 있다 — 오히려 그게 값싸다.
        print("KIPRIS_KEY 가 비어 있습니다 → ③승인여부는 판정하지 않고, "
              "①연결·②경로까지만 확인합니다.")

    print("\n①② 살아 있는 경로 찾기 — 경로 × 서비스이름 × 키질의")
    found = _discover()
    if not found:
        print("\n  → 어떤 조합도 응답하지 않았습니다. KIPRISplus 마이페이지의 "
              "API 명세에 적힌 '요청 URL' 샘플을 알려주시면 그 경로로 고정하겠습니다.")
        return 2
    base, svc, kp = found
    print(f"\n  → 기준: {base}/{svc}  (키질의={kp})")

    # 키 질의 이름이 틀리면 서버는 '필수 파라미터 없음'으로 답한다 — 파라미터가
    # 틀린 것과 응답이 똑같아 구분되지 않는다. 같은 요청에 이름만 바꿔 나란히
    # 찍어 두면 어느 쪽이 원인인지 눈으로 갈린다(응답 XML 이 짧아 값싸다).
    dpath, dparams = DISCOVERY_PATH, DISCOVERY_PARAMS
    print("\n   키 질의 이름 비교 — 같은 요청, 이름만 바꿔서")
    for cand in KEY_PARAMS:
        code, body, err = _fetch(_url(base, dpath, dparams, cand))
        v, why = _verdict(code, body, err)
        print(f"     · {cand:11s} [{v}] {why}")
    # 키를 아예 빼면 '키가 없을 때의 오류'가 무엇인지 알 수 있다 → 위 셋과 비교해
    # 우리가 보낸 키가 인식은 되고 있는지 판단할 근거가 된다.
    nokey = f"{base}/{dpath}?" + urllib.parse.urlencode(dparams)
    code, body, err = _fetch(nokey)
    v, why = _verdict(code, body, err)
    print(f"     · (키 없음)   [{v}] {why}")

    _key_diagnosis(base, svc, kp)

    rows: list[tuple[str, str, str, str]] = []
    print("\n③ 서비스별 — 이 키로 승인돼 있는가")
    for entry in _paths(svc):
        # 항목이 키 질의 이름을 따로 지정할 수 있다(계열마다 다르다).
        label, path, params = entry[0], entry[1], entry[2]
        use_kp = entry[3] if len(entry) > 3 else kp
        code, body, err = _fetch(_url(base, path, params, use_kp))
        verdict, why = _verdict(code, body, err)
        # 키가 없을 때의 '인증 오류'는 실패가 아니라 **경로가 맞다는 증거**다.
        if keyless and verdict == "미승인/키오류":
            verdict = "경로확인"
        extra = _count(body) if verdict == "열림" else ""
        mark = {"열림": "✅", "경로확인": "🔎", "요청오류": "🛠️", "기간만료": "⏳",
                "미승인/키오류": "🔒", "경로없음": "❓", "연결안됨": "⛔",
                "기타오류": "⚠️"}.get(verdict, "·")
        print(f"\n {mark} [{verdict}] {label}")
        print(f"    {path}")
        print(f"    {why}" + (f" · {extra}" if extra else ""))
        # 응답 본문은 판정과 무관하게 찍는다. 오류 XML 도 짧고, 거기에 '어느
        # 파라미터가 문제인지'가 적혀 오는 경우가 있어 다음 수를 정하는 근거가 된다.
        if verdict != "경로없음":
            # 해외 응답은 목록 뒤에 집계(totalSearchCount)가 붙고, 목록이 길어
            # 앞부분만 봐서는 그 값도 '한 쪽에 몇 건인지'도 보이지 않는다.
            # 세어서 먼저 알려 주고, 꼬리도 같이 찍는다.
            n_sr = body.count("<searchResult>")
            if n_sr or "totalSearchCount" in body:
                tail = ""
                for t in ("totalSearchCount", "colString"):
                    m = re.search(rf"<{t}>(.*?)</{t}>", body, re.S)
                    if m:
                        tail += f" · {t}={m.group(1).strip() or '(빈값)'}"
                ids = re.findall(r"<ltrtno>(.*?)</ltrtno>", body)
                print(f"    ▸ searchResult {n_sr}건{tail}")
                if ids:
                    print(f"    ▸ 문헌번호 앞 3건: {', '.join(ids[:3])}")
            # 찾는 필드가 응답 뒤쪽에 있으면 '앞부분'으로는 영영 안 보인다.
            # 국내 서지상세가 그랬다 — 청구항 26개가 앞을 다 먹어, 출원인 블록이
            # 4천 자 밖으로 밀렸다. 머리 길이를 키우면 로그만 커지고 읽기는 더
            # 어려워지므로, 볼 태그를 찍어서 그 블록만 뽑는다(KIPRIS_PICK).
            # 먼저 태그 '이름'을 전부 훑는다. 값을 짝지어 뽑으려고
            # <(\w+)>(.*?)</\1> 를 쓰면 바깥 <response> 가 문서 전체를 한 번에
            # 삼켜 결과가 늘 한 개다 — 처음에 그렇게 짜서 '그런 태그가 없다'는
            # 정반대 결론을 낼 뻔했다. 이름은 여는 태그만 세고, 값은 이름별로 따로 뽑는다.
            if PICK:
                names = sorted(set(re.findall(r"<(\w+)[\s/>]", body)))
                hit = [n for n in names if re.search(PICK, n, re.I)]
                print(f"    ── 태그 이름 {len(names)}종 · /{PICK}/ 에 걸린 것 "
                      f"{len(hit)}종 " + "-" * 12)
                print("      [전체] " + " ".join(names))
                for n in hit[:20]:
                    vals = re.findall(rf"<{n}>(.*?)</{n}>", body, re.S)
                    show = " | ".join(_squash(v, 120) or "(빈값)" for v in vals[:4])
                    print(f"      <{n}> ×{len(vals)}: {show[:300]}")
                if not hit:
                    print("      (걸린 이름 없음 — 위 [전체] 목록에서 직접 고르세요)")
            print("    ── 응답 앞부분 " + "-" * 40)
            print("    " + _squash(body, HEAD))
        rows.append((mark, verdict, label, path))

    print("\n" + "=" * 66)
    print("요약")
    for mark, verdict, label, path in rows:
        print(f"  {mark} {verdict:12s} {label}  [{path}]")
    ok = [r for r in rows if r[1] == "열림"]
    print(f"\n열린 서비스 {len(ok)}개 / 시험 {len(rows)}개 · "
          f"기준 {base}/{svc} (키질의={kp})")
    print("판정 읽는 법:  ✅ 쓸 수 있다 · 🔎 경로는 맞다(키 없이 확인) · "
          "🛠️ 우리 요청이 틀렸다(파라미터 교정) · ⏳ 키는 인식됐고 활용기간이 아니다"
          "(포털에서 기간 확인) · 🔒 승인이 없다(추가 신청) · ❓ 그 경로가 없다 · "
          "⛔ 연결 자체가 안 된다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
