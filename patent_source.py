"""전력 특허 수집 — EPO OPS(공식 API) 기반, 출원인 × 분야(CPC).

무키 Google Patents 프런트엔드에서 EPO OPS 공식 API 로 전환했다:
  - 실행당 ~100요청 차단이 없어 회전 수집 불필요(출원인당 1~2요청).
  - 응답에 CPC 가 있어 분야 분류가 정확하다(제목 키워드 추정 → CPC 분류).
  - @country 로 실제 발행국(US/KR/CN/JP/EP/DE...)을 얻어 발행국별 매트릭스가 정확하다.

질의: pa="<출원인>" and pd within "<시작> <끝>" and (cpc="H02J" or cpc="H02M" ...)
      → 최근 N일 발행 + 전력 CPC 특허를 출원인별로 수집 → CPC 로 8개 분야에 분류.

키는 GitHub Secret(OPS_KEY/OPS_SECRET). 키가 없거나 실패하면 MOCK 으로 폴백해
뉴스 탭과 사이트 빌드는 항상 정상 동작한다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import patent_config as cfg

AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"


# ── OPS 접근 ─────────────────────────────────────────────────────
def _get_token() -> str:
    cred = base64.b64encode(f"{cfg.OPS_KEY}:{cfg.OPS_SECRET}".encode()).decode()
    req = urllib.request.Request(
        AUTH_URL, data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + cred,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=cfg.REQUEST_TIMEOUT) as r:
        return json.loads(r.read())["access_token"]


def _search(token: str, cql: str, start: int, end: int) -> dict:
    url = SEARCH_URL + "?q=" + urllib.parse.quote(cql)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "X-OPS-Range": f"{start}-{end}"})
    with urllib.request.urlopen(req, timeout=cfg.REQUEST_TIMEOUT) as r:
        return json.loads(r.read())


# ── 응답 파싱 (OPS JSON 은 값이 {"$": ...} 로 감싸여 있다) ──────────
def _v(node, *path):
    cur = node
    for p in path:
        if cur is None:
            return None
        cur = cur.get(p) if isinstance(cur, dict) else None
    if isinstance(cur, dict) and "$" in cur:
        return cur["$"]
    return cur


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _docs(data: dict) -> list[dict]:
    try:
        sr = data["ops:world-patent-data"]["ops:biblio-search"]["ops:search-result"]
    except Exception:
        return []
    out = []
    for d in _as_list(sr.get("exchange-documents")):
        doc = d.get("exchange-document") if isinstance(d, dict) else None
        for one in _as_list(doc):
            if isinstance(one, dict):
                out.append(one)
    return out


def _title(bib: dict) -> str:
    for t in _as_list(bib.get("invention-title")):
        if isinstance(t, dict) and t.get("@lang") == "en":
            return str(_v(t) or "").strip()
    for t in _as_list(bib.get("invention-title")):   # 영어가 없으면 아무거나
        if isinstance(t, dict):
            return str(_v(t) or "").strip()
    return ""


def _applicant_name(bib: dict) -> str:
    apps = _as_list(_v(bib, "parties", "applicants", "applicant"))
    orig, epo = "", ""
    for a in apps:
        if not isinstance(a, dict):
            continue
        nm = str(_v(a, "applicant-name", "name") or "").strip()
        if a.get("@data-format") == "original" and nm:
            orig = nm
        elif nm and not epo:
            epo = nm
    return orig or epo


def _cpc_codes(bib: dict) -> list[str]:
    """patent-classifications(CPC) + IPC 를 'H02J3' 형태 문자열로."""
    out = []
    for pc in _as_list(_v(bib, "patent-classifications", "patent-classification")):
        if not isinstance(pc, dict):
            continue
        sec, cls, sub = _v(pc, "section"), _v(pc, "class"), _v(pc, "subclass")
        grp = _v(pc, "main-group")
        if sec and cls and sub:
            out.append(f"{sec}{cls}{sub}{grp or ''}")
    for ip in _as_list(_v(bib, "classifications-ipcr", "classification-ipcr")):
        if isinstance(ip, dict):
            txt = str(_v(ip, "text") or "")
            m = re.match(r"([A-H]\d{2}[A-Z])\s*(\d+)", txt.replace(" ", " "))
            if m:
                out.append(m.group(1) + m.group(2))
    return out


def _pub_ref(bib: dict) -> tuple[str, str, str]:
    """(발행국, 문서번호, 발행일 YYYY-MM-DD)."""
    country = number = date = ""
    for d in _as_list(_v(bib, "publication-reference", "document-id")):
        if not isinstance(d, dict):
            continue
        if d.get("@document-id-type") == "docdb":
            country = str(_v(d, "country") or "")
            number = country + str(_v(d, "doc-number") or "") + str(_v(d, "kind") or "")
            date = str(_v(d, "date") or "")
        elif d.get("@document-id-type") == "epodoc" and not number:
            number = str(_v(d, "doc-number") or "")
            date = date or str(_v(d, "date") or "")
    if len(date) == 8 and date.isdigit():
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    return country, number, date


def _classify(cpcs: list[str]) -> str | None:
    """CPC 접두로 분야 결정(CATEGORIES 순서 = 우선순위). 해당 없으면 None."""
    for cat in cfg.CATEGORIES:
        for pref in cat["match"]:
            if any(c.startswith(pref) for c in cpcs):
                return cat["key"]
    return None


def _normalize(doc: dict) -> dict | None:
    bib = doc.get("bibliographic-data") or {}
    country, number, date = _pub_ref(bib)
    if not number:
        return None
    cpcs = _cpc_codes(bib)
    cat = _classify(cpcs)
    if not cat:
        return None                      # 전력 분야로 분류 안 되면 제외
    return {
        "number": number,
        # 일부 번역 공보(ES 등)는 영문·자국어 제목이 모두 비어 온다 → 번호로 대체.
        "title": _title(bib) or number,
        "assignee": _applicant_name(bib),
        "inventor": "",
        "pub_date": date or None,
        "filing_date": None,
        "snippet": "",
        "office": country,               # 실제 발행 특허청
        "cpc": cpcs[:6],
        "category": cat,
        "url": f"https://worldwide.espacenet.com/patent/search?q={urllib.parse.quote(number)}",
    }


# ── 수집 ─────────────────────────────────────────────────────────
def _cql(applicant_q: str, days: int, today: datetime | None = None) -> str:
    # 기준일은 호출부(build_site)가 주는 KST 기준 '오늘'. utcnow() 는 3.12 에서
    # deprecated 이고 프로젝트의 다른 날짜 처리(KST)와도 어긋난다.
    end = (today or datetime.now()).date()
    start = end - timedelta(days=days)
    cpc_or = " or ".join(f'cpc="{c}"'
                         for cat in cfg.CATEGORIES for c in cat["cpc"])
    return (f'pa="{applicant_q}" and pd within '
            f'"{start.strftime("%Y%m%d")} {end.strftime("%Y%m%d")}" and ({cpc_or})')


def _live_collect(today: datetime | None = None) -> list[dict]:
    if not (cfg.OPS_KEY and cfg.OPS_SECRET):
        raise RuntimeError("OPS 키 없음(OPS_KEY/OPS_SECRET)")
    token = _get_token()
    collected: list[dict] = []
    seen: set[str] = set()
    errors = 0
    for ap in cfg.APPLICANTS:
        added = 0
        cql = _cql(ap["q"], cfg.LOOKBACK_DAYS, today)
        start = 1
        # start 상한도 함께 검사한다. CPC 분류에서 일부가 걸러지면 added 가 상한에
        # 못 미친 채 루프가 한 번 더 돌아 '51-50' 같은 역전 범위를 요청하게 된다.
        while added < cfg.PER_APPLICANT_LIMIT and start <= cfg.PER_APPLICANT_LIMIT:
            end = min(start + 24, cfg.PER_APPLICANT_LIMIT)   # OPS 범위는 25건 단위
            try:
                data = _search(token, cql, start, end)
            except Exception as e:
                # 결과 없음(404)·범위 초과는 정상 종료로 취급
                msg = str(e)
                if "404" not in msg and "400" not in msg:
                    errors += 1
                    print(f"  ! [{ap['name']}] 검색 실패: {e}")
                break
            docs = _docs(data)
            if not docs:
                break
            for d in docs:
                it = _normalize(d)
                if not it:
                    continue
                key = it["number"].upper()
                if key in seen:
                    continue
                seen.add(key)
                it["applicant"] = ap["name"]
                it["country"] = ap["region"]     # 지역 그룹(표시축)
                it["flag"] = ap["flag"]
                collected.append(it)
                added += 1
                if added >= cfg.PER_APPLICANT_LIMIT:
                    break
            if len(docs) < (end - start + 1):
                break
            start = end + 1
            if cfg.REQUEST_DELAY:
                time.sleep(cfg.REQUEST_DELAY)
        print(f"  · {ap['flag']} {ap['name']} ({ap['region']}): {added}건")
        if cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY)
    if not collected and errors:
        raise RuntimeError("OPS 수집 실패(모든 출원인)")
    return collected


# ── MOCK (키 없음/오프라인 폴백) ──────────────────────────────────
_AP_FIELDS = {
    "General Electric": ["nuclear", "grid", "industry"], "GE Vernova": ["grid", "nuclear"],
    "Eaton": ["industry", "datacenter"], "Caterpillar": ["supply"], "Dynapower": ["mega"],
    "한국전력공사": ["grid", "supply", "meter"], "한국전력기술": ["nuclear"],
    "HD현대일렉트릭": ["grid", "industry"], "효성중공업": ["grid", "industry"],
    "LS일렉트릭": ["grid", "industry", "meter"], "삼성전자": ["mega", "renew", "datacenter"],
    "일진전기": ["grid"], "대한전선": ["grid"], "산일전기": ["grid"], "제룡전기": ["grid"],
    "그리드위즈": ["supply", "meter"],
    "State Grid": ["grid", "supply", "meter"], "Huawei": ["datacenter", "mega"],
    "CATL": ["renew"], "Hitachi Energy": ["grid", "industry"],
    "Mitsubishi Electric": ["mega", "grid"], "Toshiba": ["nuclear", "supply"],
    "Panasonic": ["renew", "meter"], "Kyocera": ["supply", "renew"], "Toyota": ["renew"],
    "Sumitomo Electric": ["grid"], "Furukawa Electric": ["grid"],
    "Siemens": ["grid", "industry", "supply"], "ABB": ["grid", "industry"],
    "Schneider Electric": ["datacenter", "industry", "supply"], "Bosch": ["mega"],
}
_PREFIX = {"US": "US", "KR": "KR", "CN": "CN", "JP": "JP", "EU": "EP"}


def _mock_collect(today: datetime) -> list[dict]:
    seed = int(hashlib.md5(today.strftime("%Y-%m-%d").encode()).hexdigest()[:8], 16)
    collected = []
    for ai, ap in enumerate(cfg.APPLICANTS):
        for fi, fkey in enumerate(_AP_FIELDS.get(ap["name"], ["grid"])):
            cat = cfg.CATEGORY_BY_KEY.get(fkey)
            if not cat:
                continue
            days_ago = (seed + ai * 3 + fi * 5) % max(1, cfg.LOOKBACK_DAYS)
            pub = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            serial = 10000 + (seed + ai * 37 + fi * 101) % 90000
            num = f"{_PREFIX.get(ap['region'], 'US')}{serial}A1"
            collected.append({
                "number": num,
                "title": f"[{ap['name']}] {cat['name']} 관련 장치 및 방법",
                "assignee": ap["name"], "inventor": "",
                "pub_date": pub, "filing_date": None,
                "snippet": "[샘플 데이터] 키 없음/오프라인 환경의 미리보기용 항목입니다.",
                "office": _PREFIX.get(ap["region"], "US"),
                "cpc": cat["cpc"][:2],
                "category": fkey, "applicant": ap["name"],
                "country": ap["region"], "flag": ap["flag"],
                # URL 은 저장/읽음 상태의 키라서 항목마다 달라야 한다(전부 같으면 한꺼번에 토글).
                "url": "https://worldwide.espacenet.com/patent/search?q=" + num,
            })
    return collected


def collect(today: datetime) -> tuple[list[dict], bool]:
    """(출원인×분야) 특허 목록과 mock 여부를 반환.

    PATENT_MOCK=on → mock / off → 라이브(실패 시 예외) / auto → 라이브 후 실패 시 mock.
    """
    if cfg.is_mock():
        return _mock_collect(today), True
    try:
        return _live_collect(today), False
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ 특허 라이브(OPS) 수집 실패 → MOCK 폴백: {e}")
        return _mock_collect(today), True
