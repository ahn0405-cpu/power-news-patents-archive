"""전력 특허 수집 — KIPRISplus(국내 공보) 기반.

patent_source(EPO OPS) 와 **같은 계약**을 지킨다:
    collect(today)         -> (목록, mock여부, {"totals":…, "offices":…})
    collect_offices(today)  -> {}  (아래 '특허청 축' 항목 참고)
그래서 build_site·site_render 는 한 줄도 고치지 않고 백엔드만 바꿔 끼울 수 있다.

OPS 와 결정적으로 다른 점 — 수집 축이 뒤집힌다
--------------------------------------------------
OPS 는 pa=<출원인> 이 사실상 필수라, 우리가 미리 정한 65곳 안에서만 볼 수 있었다.
그래서 "그 분야에 국내 중소기업·대학이 있는가" 를 물어도 답이 나오지 않았고
(데이터센터 국내 지분 0% 가 '없다'인지 '안 보고 있다'인지 구분 불가), 목록을
늘려 보려던 probe_supply 는 OPS 쿼터에 막혔다.

KIPRISplus 는 **출원인 없이 분야(IPC)+기간만으로** 조회된다(실측 8/25). 그래서
이 모듈은 분야를 훑고 출원인을 **사후에 모은다**. 우리가 이름조차 모르던 곳이
스스로 드러난다 — 첫 응답에 '인천대학교 산학협력단'이 나왔다.

분류 축의 한계(정직하게 남긴다)
--------------------------------------------------
국내 항목별검색에는 cpcNumber 파라미터가 **없다**(실측: INVALID_REQUEST_PARAMETER).
우리 8대 분야는 CPC 접두로 정의돼 있으므로 Y04S(스마트그리드 ICT)는 그대로 쓸 수
없어 H02J13·G01R21/22 로 갈아 끼웠다(patent_config 주석 참고). 나머지 일곱 분야는
CPC 와 IPC 가 같은 코드라 영향이 없다.

특허청(office) 축
--------------------------------------------------
이 소스는 국내 공보다 — 전부 KR 이다. '어느 시장에 출원했나' 축은 여기서 만들 수
없으므로 collect_offices 는 빈 dict 을 돌려준다(= 기존 stats 유지). 그 축은 해외
데이터(ForeignPatentGeneralSearchService)를 붙일 때 다시 세운다.

키는 GitHub Secret(KIPRIS_KEY) → 환경변수로만 받는다. 파일에 적지 않는다.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import patent_config as cfg


# ── 요청 ─────────────────────────────────────────────────────────
def _url(op: str, params: dict) -> str:
    q = dict(params)
    q[cfg.KIPRIS_KEYPARAM] = cfg.KIPRIS_KEY
    return (f"{cfg.KIPRIS_BASE}/{cfg.KIPRIS_SERVICE}/{op}?"
            + urllib.parse.urlencode(q))


def _get(op: str, params: dict, timeout: int | None = None) -> ET.Element:
    req = urllib.request.Request(_url(op, params), headers={
        "User-Agent": "ip-power/1.0", "Accept": "application/xml"})
    try:
        with urllib.request.urlopen(
                req, timeout=timeout or cfg.REQUEST_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"KIPRIS HTTP {e.code}") from None
    # KIPRIS 는 없는 경로에 HTTP 200 + 포털 HTML 을 돌려준다(실측). XML 파싱이
    # 깨지는 것으로 드러나므로, 오류 문구를 사람이 읽을 수 있게 바꿔 준다.
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        head = raw[:120].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(f"XML 이 아닌 응답(경로가 틀렸을 수 있다): {head}") from None
    code = (root.findtext(".//resultCode") or "").strip()
    if code and code not in ("00", "0"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        raise RuntimeError(f"KIPRIS resultCode={code} {msg}")
    return root


def _total(root: ET.Element) -> int:
    try:
        return int((root.findtext(".//totalCount") or "0").strip() or 0)
    except ValueError:
        return 0


# ── 응답 파싱 ────────────────────────────────────────────────────
def _text(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def _date(v: str) -> str | None:
    """'20190612' 도 '1985/12/30 00:00:00' 도 온다(명세 샘플과 실응답이 다르다)."""
    v = (v or "").strip()
    if not v:
        return None
    digits = "".join(ch for ch in v if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def _ipcs(v: str) -> list[str]:
    """'H02J 3/38|H02M 7/48' → ['H02J3', 'H02M7'] (접두 비교용으로 정규화)."""
    out = []
    for part in (v or "").replace(",", "|").split("|"):
        s = part.strip().replace(" ", "")
        if not s:
            continue
        # 'H02J3/38' 에서 메인그룹까지만 남긴다 → 'H02J3'
        head = s.split("/")[0]
        if head and head not in out:
            out.append(head)
    return out


def _classify(ipcs: list[str], fallback: str) -> str:
    """응답 IPC 로 분야를 다시 정한다(CATEGORIES 순서 = 우선순위).

    조회는 분야별로 하지만, 한 특허가 여러 IPC 를 갖고 있으면 조회한 분야가
    그 특허의 대표 분야가 아닐 수 있다. OPS 때와 같은 규칙으로 되분류하고,
    어디에도 안 걸리면 조회한 분야를 쓴다(그 코드로 잡혔으니 근거는 있다).
    """
    for cat in cfg.CATEGORIES:
        for pref in cat["match"]:
            if any(c.startswith(pref) for c in ipcs):
                return cat["key"]
    return fallback


# 큐레이션 목록의 표기(한글·영문)를 출원인 이름과 맞춰 지역·국기를 붙인다.
# 목록에 없으면 국내 주체로 본다 — 국내 공보이므로 대부분 그렇고, 해외 기업이
# 한국에 낸 건은 아래 별칭에 걸린다.
_ALIAS: list[tuple[str, dict]] = []
for _ap in cfg.APPLICANTS:
    _names = [_ap["name"]] + (
        [_ap["q"]] if isinstance(_ap["q"], str) else list(_ap["q"]))
    for _n in _names:
        _ALIAS.append((_n.lower(), _ap))
_ALIAS.sort(key=lambda x: -len(x[0]))       # 긴 표기를 먼저 본다


def _identify(name: str) -> tuple[str, str, str]:
    """(표시명, 지역, 국기). 큐레이션에 없으면 이름 그대로 · KR."""
    low = (name or "").lower()
    for token, ap in _ALIAS:
        if token and token in low:
            return ap["name"], ap["region"], ap["flag"]
    return (name or "미상"), "KR", "🇰🇷"


def _normalize(item: ET.Element, cat_key: str) -> dict | None:
    num = _text(item, "openNumber") or _text(item, "publicationNumber") \
        or _text(item, "registerNumber") or _text(item, "applicationNumber")
    if not num:
        return None
    ipcs = _ipcs(_text(item, "ipcNumber"))
    raw_name = _text(item, "applicantName")
    name, region, flag = _identify(raw_name)
    # 초록은 카드 미리보기용으로만 쓴다 — 길면 화면이 무너진다.
    snippet = _text(item, "astrtCont")
    return {
        "number": num,
        "title": _text(item, "inventionTitle") or num,
        "assignee": raw_name,
        "inventor": "",
        # 공개일이 원칙, 없으면 공고일. 등록만 되고 공개가 없는 건도 있다.
        "pub_date": (_date(_text(item, "openDate"))
                     or _date(_text(item, "publicationDate"))
                     or _date(_text(item, "registerDate"))),
        "filing_date": _date(_text(item, "applicationDate")),
        "snippet": snippet[:180] + ("…" if len(snippet) > 180 else ""),
        "office": "KR",
        # CPC 보강은 공개번호가 아니라 **출원번호**로 조회한다 → 따로 들고 있는다.
        "filing_no": _text(item, "applicationNumber"),
        "cpc": ipcs[:6],
        "category": _classify(ipcs, cat_key),
        "applicant": name,
        "country": region,
        "flag": flag,
        # KIPRIS 공보 상세는 외부에서 열리지 않아(실측) 구글 특허로 우회한다.
        # url 은 카드 링크이자 '읽음' 상태의 키라서 항목마다 달라야 한다.
        "url": "https://patents.google.com/?q="
               + urllib.parse.quote(num) + "&hl=ko",
        "registerStatus": _text(item, "registerStatus"),
    }


# ── 수집 ─────────────────────────────────────────────────────────
def _window(today: datetime) -> str:
    """공개일 범위. KIPRIS 표기는 'YYYYMMDD~YYYYMMDD' (실측으로 확인)."""
    end = (today or datetime.now()).date()
    start = end - timedelta(days=cfg.LOOKBACK_DAYS)
    return f"{start.strftime('%Y%m%d')}~{end.strftime('%Y%m%d')}"


def _sweep_category(cat: dict, window: str) -> tuple[list[dict], int]:
    """한 분야를 훑는다. (항목, 그 분야 전체 건수).

    분야마다 IPC 접두가 여러 개일 수 있어 접두별로 조회하고 공개번호로 합친다.
    전체 건수는 접두별 totalCount 의 합이라 겹치는 문서만큼 부풀 수 있다 —
    같은 특허가 H02G 와 H01F27 을 동시에 갖고 있으면 두 번 세어진다. 목록은
    dedup 하지만 합계는 그럴 수 없으므로, 접두가 하나인 분야에서만 정확하다.
    """
    got: dict[str, dict] = {}
    total = 0
    for pref in cat.get("ipc") or cat["cpc"]:
        page, taken = 1, 0
        while taken < cfg.KIPRIS_PER_CAT:
            params = {
                "ipcNumber": pref,
                "openDate": window,
                "patent": "true",
                "utility": "false",
                "numOfRows": str(cfg.KIPRIS_ROWS),
                "pageNo": str(page),
            }
            try:
                root = _get("getAdvancedSearch", params)
            except Exception as e:
                print(f"  ! [{cat['name']}/{pref}] {e}")
                break
            if page == 1:
                total += _total(root)
            items = root.findall(".//item")
            if not items:
                break
            for it in items:
                one = _normalize(it, cat["key"])
                if one and one["number"] not in got:
                    got[one["number"]] = one
                    taken += 1
            if len(items) < cfg.KIPRIS_ROWS:
                break
            page += 1
            if cfg.KIPRIS_DELAY:
                time.sleep(cfg.KIPRIS_DELAY)
    return list(got.values()), total


# ── CPC 보강 ─────────────────────────────────────────────────────
# IPC 로 대체한 분야(계량·스마트그리드)는 원래 Y04S 로 정의돼 있었다. 그 코드는
# IPC 에 없어 검색으로는 못 잡지만, 출원번호로 CPC 를 되받아 분류를 바로잡을 수는
# 있다. '대체 접두로 잡힌 건'이 분류가 바뀔 여지가 가장 크므로 그것부터 채운다.
_SUBSTITUTE = ("G01R21", "G01R22", "H02J13")


def _cpc_of(application_no: str) -> list[str]:
    """출원번호 하나의 CPC 목록. 실패하면 빈 목록(보강은 '있으면 좋은' 것)."""
    q = {"applicationNumber": application_no,
         cfg.KIPRIS_CPC_KEYPARAM: cfg.KIPRIS_KEY}
    url = (f"{cfg.KIPRIS_CPC_BASE}/{cfg.KIPRIS_SERVICE}/patentCpcInfo?"
           + urllib.parse.urlencode(q))
    req = urllib.request.Request(url, headers={
        "User-Agent": "ip-power/1.0", "Accept": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=cfg.OFFICE_TIMEOUT) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return []
    out: list[str] = []
    for node in root.iter("CooperativepatentclassificationNumber"):
        for code in _ipcs(node.text or ""):
            if code not in out:
                out.append(code)
    return out


def _enrich_cpc(items: list[dict]) -> int:
    """상한 안에서 CPC 를 받아 분야를 다시 정한다. 반환: 분류가 바뀐 건수."""
    if cfg.KIPRIS_CPC_LIMIT <= 0 or not items:
        return 0
    # 대체 접두로 잡힌 것 먼저, 그다음 최신순.
    def _pri(it: dict) -> tuple[int, str]:
        sub = any(c.startswith(p) for c in it["cpc"] for p in _SUBSTITUTE)
        return (0 if sub else 1, it.get("pub_date") or "")
    order = sorted(items, key=_pri)[:cfg.KIPRIS_CPC_LIMIT]
    changed = 0
    for it in order:
        appno = (it.get("filing_no") or "").strip() or it["number"]
        cpc = _cpc_of(appno)
        if not cpc:
            continue
        merged = cpc + [c for c in it["cpc"] if c not in cpc]
        before = it["category"]
        it["cpc"] = merged[:6]
        it["category"] = _classify(merged, before)
        if it["category"] != before:
            changed += 1
        if cfg.KIPRIS_DELAY:
            time.sleep(cfg.KIPRIS_DELAY)
    skipped = len(items) - len(order)
    print(f"  CPC 보강 {len(order)}건 · 분류 정정 {changed}건"
          + (f" · 상한으로 건너뜀 {skipped}건" if skipped else ""))
    return changed


def _live_collect(today: datetime) -> tuple[list[dict], dict]:
    if not cfg.KIPRIS_KEY:
        raise RuntimeError("KIPRIS 키 없음(KIPRIS_KEY)")
    window = _window(today)
    print(f"KIPRIS 국내 공보 수집 — 공개일 {window}")
    collected: list[dict] = []
    seen: set[str] = set()
    cat_totals: dict[str, int] = {}

    for cat in cfg.CATEGORIES:
        items, total = _sweep_category(cat, window)
        cat_totals[cat["key"]] = total
        added = 0
        for it in items:
            if it["number"] in seen:
                continue
            seen.add(it["number"])
            collected.append(it)
            added += 1
        print(f"  · {cat['emoji']} {cat['name']}: {added}건 수집 "
              f"(조건에 맞는 전체 {total}건)")
        if cfg.KIPRIS_DELAY:
            time.sleep(cfg.KIPRIS_DELAY)

    if not collected:
        raise RuntimeError("KIPRIS 수집 0건 — 질의나 기간을 확인해야 한다")

    # 총계를 세기 전에 보강한다 — 분류가 바뀌면 분야 분포도 따라 바뀌어야 한다.
    _enrich_cpc(collected)

    # 출원인 총계는 따로 조회하지 않고 **모은 것을 센다**.
    #
    # 처음에는 출원인마다 count 질의를 한 번씩 더 던지려 했는데, 그 값은 그
    # 출원인의 '전 분야' 건수라 전력 분야로 한정된 지금 지표(CR3·실질 경쟁자 수)와
    # 성격이 어긋난다. 분야별로 다시 나누려면 출원인당 열몇 번을 더 던져야 한다.
    #
    # KIPRIS 는 OPS 같은 쿼터 압박이 없어서 애초에 그럴 필요가 없다 — 기간 안의
    # 모집단을 통째로 받으면 '표본'이 곧 전수이고, 세면 그게 정확한 총계다.
    # 이 한 수로 OPS 시절 우리를 세 번 물었던 표본 편향(출원인당 상한 절단,
    # 재생에너지 지분이 31%→74% 로 뒤집힌 일)이 통째로 사라진다.
    totals: dict[str, int] = {}
    for it in collected:
        totals[it["applicant"]] = totals.get(it["applicant"], 0) + 1

    # 다만 상한에 걸려 잘린 분야가 있으면 그 분야의 총계는 전수가 아니다.
    # 조용히 넘어가면 '전부 봤다'로 읽히므로 반드시 남긴다.
    short = [c["name"] for c in cfg.CATEGORIES
             if cat_totals.get(c["key"], 0) > cfg.KIPRIS_PER_CAT]
    if short:
        print(f"  ⚠️ 상한({cfg.KIPRIS_PER_CAT}건)에 걸린 분야: {', '.join(short)}"
              " — 이 분야의 출원인 총계는 전수가 아니다(KIPRIS_PER_CAT 를 올릴 것)")

    print(f"  합계 {len(collected)}건 · 출원인 {len(totals)}곳")
    return collected, {"totals": totals, "categoryTotals": cat_totals,
                       "truncated": short}


# ── 계약 맞추기 ──────────────────────────────────────────────────
def collect(today: datetime) -> tuple[list[dict], bool, dict]:
    """(특허 목록, mock 여부, 집계). patent_source.collect 와 같은 계약."""
    if cfg.is_mock():
        import patent_source                 # MOCK 은 기존 것을 그대로 쓴다
        items, stats = patent_source._mock_collect(today)
        return items, True, stats
    try:
        items, stats = _live_collect(today)
        return items, False, stats
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ KIPRIS 수집 실패 → MOCK 폴백: {e}")
        import patent_source
        items, stats = patent_source._mock_collect(today)
        return items, True, stats


def collect_offices(today: datetime) -> dict:
    """국내 공보만 다루므로 특허청 축은 만들지 않는다 → 기존 stats 를 유지.

    빈 dict 을 돌려주면 patent_archive.merge_stats 가 아무것도 덮어쓰지 않는다.
    해외 데이터를 붙일 때 이 자리를 다시 채운다.
    """
    return {}
