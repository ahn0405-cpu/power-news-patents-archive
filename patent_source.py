"""전력 특허 수집 — EPO OPS(공식 API) 기반, 출원인 × 분야(CPC).

무키 Google Patents 프런트엔드에서 EPO OPS 공식 API 로 전환했다:
  - 실행당 ~100요청 차단이 없어 회전 수집 불필요(출원인당 1~2요청).
  - 응답에 CPC 가 있어 분야 분류가 정확하다(제목 키워드 추정 → CPC 분류).
  - @country 로 실제 발행국(US/KR/CN/JP/EP/DE...)을 얻어 발행국별 매트릭스가 정확하다.

질의: pa="<출원인>" and pd within "<시작> <끝>" and (cpc="H02J" or cpc="H02M" ...)
      → 최근 N일 발행 + 전력 CPC 특허를 출원인별로 수집 → CPC 로 8개 분야에 분류.

두 갈래로 나눠 돈다(OPS 무료 쿼터 보호):
  - collect()         : 특허 목록 + 출원인 총계 — 주 1회(특허 워크플로).
  - collect_offices() : 출원인×공개 특허청 정확 집계 — **매일** 일부(뉴스 워크플로에 얹어
                        날짜 기준 회전, 31곳을 ~4일에 한 바퀴). 결과는 stats.json 에 병합.

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


def _search(token: str, cql: str, start: int, end: int,
            timeout: int | None = None) -> tuple[dict, int]:
    """(응답, 조건에 맞는 전체 건수). 전체 건수는 받아온 건수와 무관하게 온다 →
    저장은 표본(상한)만 하면서도 '실제로 몇 건인지'를 정확히 알 수 있다."""
    url = SEARCH_URL + "?q=" + urllib.parse.quote(cql)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "X-OPS-Range": f"{start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout or cfg.REQUEST_TIMEOUT) as r:
        data = json.loads(r.read())
    return data, _total(data)


def _total(data: dict) -> int:
    try:
        v = data["ops:world-patent-data"]["ops:biblio-search"]["@total-result-count"]
        return int(v)
    except Exception:
        return 0


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
            m = re.match(r"([A-H]\d{2}[A-Z])\s*(\d+)", txt)
            if m:
                out.append(m.group(1) + m.group(2))
    # CPC 와 IPC 에 같은 코드가 들어 있어 그대로 두면 카드에 'F03D7 F03D7' 처럼 중복 표시된다.
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


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
def _cql(applicant_q, days: int, today: datetime | None = None) -> str:
    # 기준일은 호출부(build_site)가 주는 KST 기준 '오늘'. utcnow() 는 3.12 에서
    # deprecated 이고 프로젝트의 다른 날짜 처리(KST)와도 어긋난다.
    end = (today or datetime.now()).date()
    start = end - timedelta(days=days)
    cpc_or = " or ".join(f'cpc="{c}"'
                         for cat in cfg.CATEGORIES for c in cat["cpc"])
    # q 는 문자열 하나 또는 여러 표기(영문·한글 등)의 목록. 한국 중견기업은 OPS 색인에
    # 한글 원표기로만 잡히는 문서가 있어(실측: '산일전기' 56건 vs 'Sanil Electric' 5건)
    # 표기를 OR 로 묶어야 놓치지 않는다.
    names = [applicant_q] if isinstance(applicant_q, str) else list(applicant_q)
    pa_or = " or ".join(f'pa="{n}"' for n in names)
    return (f'({pa_or}) and pd within '
            f'"{start.strftime("%Y%m%d")} {end.strftime("%Y%m%d")}" and ({cpc_or})')


def _count(token: str, cql: str) -> int:
    """1건만 받아 전체 건수만 얻는다(집계 전용, 응답이 가볍다)."""
    _, total = _search(token, cql, 1, 1, timeout=cfg.OFFICE_TIMEOUT)
    return total


def _office_counts(token: str, ap: dict, today, deadline: float = 0.0) -> tuple[dict, bool]:
    """출원인이 어느 특허청에 몇 건 공개했는지 — '어느 시장에 출원하나' 축.

    같은 발명이 여러 특허청에 공개되므로 특허청별 합계는 출원인 총계를 넘을 수 있다.
    OPS CQL 의 pn any "<코드>" 로 공개국을 제한한다(프로브로 실측 확인).
    반환: ({특허청: 건수}, 쿼터초과여부)
    """
    out, quota_hit = {}, False
    base = _cql(ap["q"], cfg.LOOKBACK_DAYS, today)
    for off in cfg.OFFICES:
        if deadline and time.monotonic() > deadline:
            return out, True           # 시간 초과도 '중단' 취급 → 다음 날 이어서
        try:
            n = _count(token, f'{base} and pn any "{off["code"]}"')
        except Exception as e:
            if _is_quota(e):           # 쿼터 초과 → 즉시 중단(더 조르면 본 수집도 막힌다)
                return out, True
            n = 0                      # 404 = 해당 특허청 공개 없음
        if n:
            out[off["code"]] = n
        if cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY)
    return out, quota_hit


def _is_quota(e: Exception) -> bool:
    """OPS 쿼터/스로틀 거부(403). 이게 뜨면 더 조르지 않고 물러나야 한다."""
    return "403" in str(e)


def _office_batch(today) -> list[dict]:
    """이번 실행에서 특허청 집계를 돌릴 출원인 부분집합(**날짜 기준 회전**).

    전체를 한 번에 돌리면 OPS 쿼터를 넘겨 403 이 나고 본 수집까지 실패한다(실측).
    그래서 조금씩 나눠 돌리는데, 주 1회로 회전하면 31곳을 한 바퀴 도는 데 5~6주가
    걸린다 → 뉴스가 매일 도니까 거기에 얹어 **매일** 회전한다(한 바퀴 ~4일).
    """
    n = len(cfg.APPLICANTS)
    if not cfg.OFFICE_COUNTS or cfg.OFFICE_BATCH <= 0 or not n:
        return []
    doy = (today or datetime.now()).timetuple().tm_yday
    start = (doy * cfg.OFFICE_BATCH) % n
    order = cfg.APPLICANTS[start:] + cfg.APPLICANTS[:start]
    return order[:cfg.OFFICE_BATCH]


def _collect_order(today) -> list[dict]:
    """이번 주 목록 수집 순서(**주 단위 회전**).

    늘 같은 순서로 돌면 쿼터(403)가 나는 지점이 매주 같은 자리라 뒤쪽이 영구히
    굶는다. 실측(7/20·7/27·8/3 세 주) — 매번 24~28곳만 채워지고 나머지는 통째로
    빠졌으며, 8/3 은 39번(중국 끝)에서 끊겨 일본·유럽 25곳이 전부 0건이었다.
    그래서 Siemens Energy·Nordex·Prysmian 등은 총계만 있고 목록이 한 번도
    채워지지 않았다. 시작점을 매주 옮겨 굶는 구간이 돌게 한다(3주면 한 바퀴).

    단, 회전이 dedup 우선순위 묶음을 끊으면 안 된다. q="Siemens" 는 Siemens
    Energy·Gamesa 문서까지 걸리므로 반드시 그 둘보다 뒤여야 한다("seq" 표시) →
    시작점이 묶음 안에 떨어지면 묶음 맨 앞으로 물린다.
    """
    n = len(cfg.APPLICANTS)
    if n <= 1 or cfg.COLLECT_ROTATE <= 0:
        return list(cfg.APPLICANTS)
    wk = (today or datetime.now()).isocalendar()[1]
    start = (wk * cfg.COLLECT_ROTATE) % n
    while start > 0 and cfg.APPLICANTS[start].get("seq"):
        start -= 1
    return cfg.APPLICANTS[start:] + cfg.APPLICANTS[:start]


def collect_offices(today: datetime) -> dict:
    """오늘 배치 출원인의 정확 집계만 가볍게 수집 → {"totals":…, "offices":…}.

    본 수집(특허 목록)과 분리돼 있어 **매일 뉴스 실행에 얹어** 돌린다. 출원인당
    1(총계) + 특허청 수(기본 6) 요청이라 배치가 작으면 쿼터에 여유가 있다.
    MOCK/키 없음이면 조용히 빈 dict(집계 없음 → 기존 stats.json 유지).
    """
    if cfg.is_mock() or not (cfg.OPS_KEY and cfg.OPS_SECRET):
        return {}
    batch = _office_batch(today)
    if not batch:
        return {}
    try:
        token = _get_token()
    except Exception as e:
        print(f"⚠️ 공개국 집계 건너뜀(토큰 실패): {e}")
        return {}
    totals: dict[str, int] = {}
    offices: dict[str, dict] = {}
    deadline = time.monotonic() + cfg.OFFICE_BUDGET
    print(f"공개국 집계 {len(batch)}곳: {', '.join(a['name'] for a in batch)}")
    for ap in batch:
        if time.monotonic() > deadline:
            print(f"  (시간 상한 {cfg.OFFICE_BUDGET:.0f}초 — 나머지는 내일 이어서)")
            break
        base = _cql(ap["q"], cfg.LOOKBACK_DAYS, today)
        why = ""                          # 0 이 나온 이유(진짜 0건인지, 실패인지)
        try:
            tot = _count(token, base)
        except Exception as e:
            if _is_quota(e):
                print("  (OPS 쿼터 한계 — 나머지는 내일 이어서)")
                break
            # 404 는 '해당 기간 공개 없음'이지만 타임아웃·5xx 는 일시적 실패다.
            # 둘을 똑같이 '0건'으로 찍으면 검색어가 틀린 것인지 그냥 실패한 것인지
            # 로그로 구분할 수 없다(실제로 Sumitomo 가 이렇게 묻혔다).
            tot, why = 0, ("" if "404" in str(e) else f" — 조회 실패: {e}")
        if tot:
            totals[ap["name"]] = tot
        if cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY)
        if not tot:
            print(f"  · {ap['flag']} {ap['name']}: 0건{why}")
            continue
        oc, hit = _office_counts(token, ap, today, deadline)
        if oc:
            offices[ap["name"]] = oc
        print(f"  · {ap['flag']} {ap['name']}: {tot}건 · "
              + (" ".join(f"{k}{v}" for k, v in oc.items()) or "-"))
        if hit:
            print("  (OPS 쿼터/시간 한계 — 나머지는 내일 이어서)")
            break
    return {"totals": totals, "offices": offices}


def _live_collect(today: datetime | None = None) -> tuple[list[dict], dict]:
    if not (cfg.OPS_KEY and cfg.OPS_SECRET):
        raise RuntimeError("OPS 키 없음(OPS_KEY/OPS_SECRET)")
    token = _get_token()
    collected: list[dict] = []
    totals: dict[str, int] = {}      # 출원인 → 실제 전체 건수(상한과 무관)
    # 특허청별 집계는 여기서 하지 않는다 — 쿼터 때문에 매일 조금씩 나눠 돌린다
    # (collect_offices). 본 수집은 목록 + 출원인 총계까지만 담당.
    seen: set[str] = set()
    errors = 0
    order = _collect_order(today)
    if order and order[0] is not cfg.APPLICANTS[0]:
        print(f"  (이번 주 시작: {order[0]['name']} — 주마다 시작점을 옮겨 "
              f"쿼터에 걸리는 구간이 돌게 한다)")
    for ap in order:
        added = 0
        cql = _cql(ap["q"], cfg.LOOKBACK_DAYS, today)
        start = 1
        # start 상한도 함께 검사한다. CPC 분류에서 일부가 걸러지면 added 가 상한에
        # 못 미친 채 루프가 한 번 더 돌아 '51-50' 같은 역전 범위를 요청하게 된다.
        while added < cfg.PER_APPLICANT_LIMIT and start <= cfg.PER_APPLICANT_LIMIT:
            end = min(start + 24, cfg.PER_APPLICANT_LIMIT)   # OPS 범위는 25건 단위
            try:
                try:
                    data, total = _search(token, cql, start, end)
                except Exception as e1:
                    if not _is_quota(e1):
                        raise
                    time.sleep(8)          # 쿼터/스로틀 → 한 번만 쉬고 재시도
                    data, total = _search(token, cql, start, end)
                if total:
                    totals[ap["name"]] = total
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
        tot = totals.get(ap["name"], 0)
        cap = " (상한, 실제 %d건)" % tot if tot > added else ""
        print(f"  · {ap['flag']} {ap['name']} ({ap['region']}): {added}건{cap}")
        if cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY)
    if not collected and errors:
        raise RuntimeError("OPS 수집 실패(모든 출원인)")
    if cfg.KR_FOCUS:
        _collect_kr(token, today, seen, collected)
    # offices 는 여기서 만들지 않는다(매일 도는 collect_offices 담당) → totals 만 돌려준다.
    return collected, {"totals": totals}


def _collect_kr(token: str, today, seen: set[str], collected: list[dict]) -> int:
    """해외 출원인이 **한국에 공개한** 건만 따로 훑어 목록에 더한다.

    왜 따로 도나: 본 수집은 출원인당 상한(PER_APPLICANT_LIMIT) 안에서 최신순이라
    KR 공개가 상한 밖으로 밀려난다(실측 — CATL 은 집계상 KR 60건인데 표본엔 0건).
    해외 출원인의 국내 공개는 '그들이 한국 시장에서 지킬 값어치가 있다고 본 기술'이라
    국내 업계에 가장 쓸모 있는 신호인데, 정작 목록에서 빠져 있었다.

    국내 출원인은 건너뛴다(당연히 KR 에 낸다 — 신호가 되지 않는다).
    쿼터(403)나 시간 예산을 만나면 그 자리에서 접고 다음 주에 이어서 채운다.
    반환: 새로 더한 건수.
    """
    deadline = time.monotonic() + cfg.KR_BUDGET if cfg.KR_BUDGET else 0.0
    n_new = 0
    print("  국내(KR) 공개 추가 수집 — 해외 출원인만")
    for ap in cfg.APPLICANTS:
        if ap["region"] == "KR":
            continue
        if deadline and time.monotonic() > deadline:
            print("    (시간 예산 초과 — 나머지는 다음 주에)")
            break
        cql = _cql(ap["q"], cfg.LOOKBACK_DAYS, today) + ' and pn any "KR"'
        added, start = 0, 1
        while added < cfg.KR_LIMIT and start <= cfg.KR_LIMIT:
            end = min(start + 24, cfg.KR_LIMIT)
            try:
                data, _total = _search(token, cql, start, end)
            except Exception as e:
                if _is_quota(e):
                    print("    (쿼터 초과 — 나머지는 다음 주에)")
                    return n_new
                break                      # 404 = 국내 공개 없음
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
                it["country"] = ap["region"]
                it["flag"] = ap["flag"]
                collected.append(it)
                added += 1
                n_new += 1
                if added >= cfg.KR_LIMIT:
                    break
            if len(docs) < (end - start + 1):
                break
            start = end + 1
            if cfg.REQUEST_DELAY:
                time.sleep(cfg.REQUEST_DELAY)
        if added:
            print(f"    🇰🇷 {ap['flag']} {ap['name']}: 국내 공개 {added}건 추가")
        if cfg.REQUEST_DELAY:
            time.sleep(cfg.REQUEST_DELAY)
    print(f"  국내 공개 추가 {n_new}건")
    return n_new


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
    # MOCK 은 표본이 곧 전체이므로 총계·특허청 집계를 그대로 세어 만든다.
    totals: dict[str, int] = {}
    offices: dict[str, dict] = {}
    for it in collected:
        nm = it["applicant"]
        totals[nm] = totals.get(nm, 0) + 1
        per = offices.setdefault(nm, {})
        per[it["office"]] = per.get(it["office"], 0) + 1
    return collected, {"totals": totals, "offices": offices}


def collect(today: datetime) -> tuple[list[dict], bool, dict]:
    """(특허 목록, mock 여부, 집계 dict{totals, offices}).

    목록은 출원인당 상한(PER_APPLICANT_LIMIT)까지의 표본이지만, 전체 건수는 OPS 가
    알려주는 실제 값이라 랭킹·규모 비교는 상한에 왜곡되지 않는다.

    PATENT_MOCK=on → mock / off → 라이브(실패 시 예외) / auto → 라이브 후 실패 시 mock.
    """
    if cfg.is_mock():
        items, stats = _mock_collect(today)
        return items, True, stats
    try:
        items, stats = _live_collect(today)
        return items, False, stats
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ 특허 라이브(OPS) 수집 실패 → MOCK 폴백: {e}")
        items, stats = _mock_collect(today)
        return items, True, stats
