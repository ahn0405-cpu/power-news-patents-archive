"""수집기 스모크 테스트 — 네트워크·키 없이 '라이브 경로'를 실제로 실행해 본다.

왜 필요한가: MOCK 빌드는 _mock_collect 만 타고, 매일 도는 뉴스 실행은
collect_offices 만 탄다 → OPS 를 쓰는 _live_collect 는 주 1회 특허 워크플로에서만
실행돼, 거기 있는 오류(예: 미정의 변수)가 월요일에야 드러난다. py_compile 로는
NameError 를 못 잡으므로, OPS 응답을 흉내 내는 스텁을 끼워 두 경로를 다 돌려본다.

사용: python selftest.py   (의존성 없음. 실패하면 exit 1)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import patent_config as cfg
import patent_source as ps

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ── OPS 응답 스텁 (값이 {"$": ...} 로 감싸인 실제 구조를 그대로 흉내) ──
def _doc(number: str, date: str, cpc: str) -> dict:
    return {"exchange-document": {"bibliographic-data": {
        "publication-reference": {"document-id": [{
            "@document-id-type": "docdb",
            "country": {"$": number[:2]}, "doc-number": {"$": number[2:-2]},
            "kind": {"$": number[-2:]}, "date": {"$": date}}]},
        "invention-title": [{"@lang": "en", "$": "Stub power apparatus"}],
        "parties": {"applicants": {"applicant": [
            {"@data-format": "original",
             "applicant-name": {"name": {"$": "STUB CO LTD"}}}]}},
        "patent-classifications": {"patent-classification": [{
            "section": {"$": cpc[0]}, "class": {"$": cpc[1:3]},
            "subclass": {"$": cpc[3]}, "main-group": {"$": cpc[4:]}}]},
    }}}


class Stub:
    """_search 를 대신한다. 호출마다 다른 공개번호를 줘 중복 제거에 다 걸리지 않게."""

    def __init__(self, total: int = 7, per_call: int = 2):
        self.total, self.per_call, self.calls = total, per_call, 0

    def __call__(self, token, cql, start, end, timeout=None):
        self.calls += 1
        check_range = start <= end
        if not check_range:                       # '51-50' 같은 역전 범위 방지 확인
            raise AssertionError(f"잘못된 OPS 범위: {start}-{end}")
        docs = [_doc(f"US{9000000 + self.calls * 10 + i}A1", "20260701", "H02J3")
                for i in range(self.per_call)]
        return ({"ops:world-patent-data": {"ops:biblio-search": {
            "@total-result-count": str(self.total),
            "ops:search-result": {"exchange-documents": docs}}}}, self.total)


def _kipris_page(rows: list[tuple[int, str, str]], total: int) -> str:
    body = "".join(
        f"<item><applicantName>{who}</applicantName>"
        f"<applicationDate>20240701</applicationDate>"
        f"<applicationNumber>10202400{i:05d}</applicationNumber>"
        f"<astrtCont>초록 {i}</astrtCont><inventionTitle>제목 {i}</inventionTitle>"
        f"<ipcNumber>{ipc}</ipcNumber><openDate>20260115</openDate>"
        f"<openNumber>102026{i:07d}</openNumber>"
        f"<registerStatus>공개</registerStatus></item>"
        for i, who, ipc in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?><response><header>'
            "<resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg>"
            f"</header><body><items>{body}</items>"
            f"<count><totalCount>{total}</totalCount></count></body></response>")


def _kipris_checks() -> None:
    """KIPRIS 백엔드의 라이브 경로를 네트워크 없이 실제로 실행한다.

    py_compile 로는 못 잡는 것들을 잡으려는 것이다 — OPS 때 _live_collect 의
    NameError 가 월요일 실행에서야 드러난 적이 있다. 여기서 확인하는 것은
    '수집이 도는가'가 아니라 **site_render 가 기대하는 계약을 지키는가**다.
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime
    import patent_source_kipris as ks

    import urllib.request

    print("\n[KIPRIS 백엔드]")
    orig_get, orig_key = ks._get, cfg.KIPRIS_KEY
    orig_lim, orig_open = cfg.KIPRIS_CPC_LIMIT, urllib.request.urlopen
    cfg.KIPRIS_KEY = "TEST"
    calls: list[dict] = []
    cpc_calls: list[str] = []
    try:
        # CPC 보강은 urlopen 을 직접 쓴다 → 여기서도 막지 않으면 스모크 테스트가
        # 네트워크를 탄다(러너에서 수집 전에 도는 검사라 절대 나가면 안 된다).
        cfg.KIPRIS_CPC_LIMIT = 5
        _CPC = ('<?xml version="1.0"?><response><body><items><patentCpcInfo>'
                "<CooperativepatentclassificationNumber>Y04S 10/50"
                "</CooperativepatentclassificationNumber></patentCpcInfo>"
                "</items></body></response>")

        class _CpcResp:
            def read(self): return _CPC.encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_open(req, *a, **k):
            cpc_calls.append(getattr(req, "full_url", str(req)))
            return _CpcResp()
        urllib.request.urlopen = _fake_open

        def fake(op, params, timeout=None):
            calls.append(dict(params, _op=op))
            who = ["한국전력공사", "인천대학교 산학협력단", "주식회사 현대케피코"]
            n = len(calls)
            return ET.fromstring(_kipris_page(
                [(n * 10 + k, who[k], params["ipcNumber"]) for k in range(3)], 42))
        ks._get = fake

        items, mock, stats = ks.collect(datetime(2026, 8, 25))
        check(not mock, "라이브 경로로 돈다 (MOCK 폴백이 아니다)")
        check(bool(items), f"수집 결과가 있다 ({len(items)}건)")

        need = {"number", "title", "assignee", "pub_date", "office", "cpc",
                "category", "applicant", "country", "flag", "url"}
        missing = need - set(items[0])
        check(not missing, f"항목 스키마가 site_render 계약을 지킨다 (누락 {missing or '없음'})")

        keys = {c["key"] for c in cfg.CATEGORIES}
        check(all(i["category"] in keys for i in items),
              "모든 항목이 8대 분야 중 하나로 분류된다")
        check(all(i["office"] == "KR" for i in items), "국내 공보이므로 office 는 전부 KR")
        nums = [i["number"] for i in items]
        check(len(nums) == len(set(nums)), "공개번호가 중복되지 않는다")
        check(all(i["url"].startswith("https://") for i in items),
              "카드 링크가 전부 https 다")
        check(len({i["url"] for i in items}) == len(items),
              "url 이 항목마다 다르다 (읽음 상태의 키라 겹치면 한꺼번에 토글된다)")

        # 총계는 따로 조회하지 않고 모은 것을 센다 — 그 약속이 지켜지는지 본다.
        check(sum(stats["totals"].values()) == len(items),
              "출원인 총계의 합 = 수집 건수 (표본과 전수가 어긋나지 않는다)")
        # 전수를 가져오므로 집계는 병합이 아니라 대체여야 한다. 병합하면 OPS 시절
        # 값(전 세계·CPC 기준)이 남아 단위가 다른 수치와 한 표에 섞인다(첫 실전
        # 실행에서 실제로 그랬다 — Siemens 183 같은 옛 수치가 그대로 남았다).
        check(stats.get("replaceTotals") is True,
              "집계를 대체로 표시한다 (옛 OPS 수치가 섞이지 않는다)")
        import patent_archive as pa
        store = {"totals": {"Siemens": 183}, "updated": {"Siemens": "2026-08-03"},
                 "offices": {}}
        pa.merge_stats(store, stats, "2026-08-25")
        check("Siemens" not in store["totals"] or store["totals"]["Siemens"] != 183,
              "merge_stats 가 옛 값을 실제로 버린다")

        # 공동출원('|')과 한글 법인명이 같은 회사를 둘로 가르면 랭킹이 거짓이 된다.
        check(ks._split_applicants("현대자동차주식회사|기아 주식회사")
              == ["현대자동차주식회사", "기아 주식회사"],
              "공동출원인을 '|' 로 나눈다")
        for raw, want in (("주식회사 엘지에너지솔루션", "LG에너지솔루션"),
                          ("삼성에스디아이 주식회사", "삼성SDI"),
                          ("도요타 지도샤(주)", "Toyota"),
                          ("컨템포러리 엠퍼렉스 테크놀로지 씨오., 리미티드", "CATL")):
            check(ks._identify(raw)[0] == want,
                  f"한글 법인명이 붙는다: {raw} → {want}")
        check(all(p.get("openDate") for p in calls),
              "모든 질의에 공개일 범위가 들어간다 (기간 없이 전수를 긁지 않는다)")
        check(all("~" in p["openDate"] for p in calls),
              "공개일 범위 표기가 'YYYYMMDD~YYYYMMDD' 다 (실측 형식)")
        check(not any("cpcNumber" in p for p in calls),
              "cpcNumber 를 보내지 않는다 (이 API 에 없는 파라미터 — 실측)")

        # CPC 보강: 검색으로는 못 잡는 Y04S 를 출원번호로 되받아 분류에 반영한다.
        check(len(cpc_calls) == cfg.KIPRIS_CPC_LIMIT,
              f"CPC 보강이 상한만큼만 돈다 ({len(cpc_calls)}건)")
        check(all("patentCpcInfo" in u and "applicationNumber=" in u
                  for u in cpc_calls),
              "CPC 는 출원번호로 조회한다 (공개번호가 아니다)")
        check(all(cfg.KIPRIS_CPC_KEYPARAM + "=" in u for u in cpc_calls),
              f"CPC 조회의 키 질의 이름이 {cfg.KIPRIS_CPC_KEYPARAM} 다 (계열이 다르다)")
        enriched = [i for i in items if any(c.startswith("Y04S") for c in i["cpc"])]
        check(bool(enriched), f"보강된 건에 CPC 가 들어간다 ({len(enriched)}건)")
        check(all(i["category"] == "meter" for i in enriched),
              "Y04S 가 붙으면 계량·스마트그리드로 분류된다 (IPC 로는 못 잡는 코드)")

        # 국내 전용이라 특허청 축은 만들지 않는다. 빈 dict 이어야 기존 stats 가
        # 덮이지 않는다(merge_stats 계약).
        check(ks.collect_offices(datetime(2026, 8, 25)) == {},
              "collect_offices 는 빈 dict (기존 공개국 집계를 덮지 않는다)")

        # 경로가 틀리면 KIPRIS 는 HTTP 200 + 포털 HTML 을 준다. 그때 XML 파싱이
        # 깨지는데, 사람이 읽을 수 있는 오류로 바뀌는지 확인한다(실측으로 물렸던 함정).
        ks._get = orig_get
        class _Fake:
            def read(self): return b"<!doctype html><html>\xed\x8e\x98\xec\x9d\xb4\xec\xa7\x80"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        saved_open = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Fake()
        try:
            ks._get("getAdvancedSearch", {"ipcNumber": "H02M"})
            check(False, "포털 HTML 응답이 오류로 드러난다")
        except RuntimeError as e:
            check("XML" in str(e), f"포털 HTML 응답이 오류로 드러난다 ({e})")
        finally:
            urllib.request.urlopen = saved_open
    finally:
        ks._get, cfg.KIPRIS_KEY = orig_get, orig_key
        cfg.KIPRIS_CPC_LIMIT = orig_lim
        urllib.request.urlopen = orig_open


def main() -> int:
    today = datetime(2026, 7, 27)
    orig = (ps._search, ps._get_token, cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY)
    ps._get_token = lambda: "stub-token"
    cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = "k", "s", 0.0
    try:
        print("· _live_collect (주간 특허 수집 경로)")
        stub = Stub()
        ps._search = stub
        items, stats = ps._live_collect(today)
        check(bool(items), f"특허를 수집한다 ({len(items)}건)")
        check(isinstance(stats.get("totals"), dict) and bool(stats["totals"]),
              f"출원인 총계를 담는다 ({len(stats.get('totals', {}))}곳)")
        check(all(i.get("category") and i.get("number") for i in items),
              "모든 항목에 분야·공개번호가 있다")

        print("· 국내(KR) 공개 추가 수집 (해외 출원인 한정)")
        # KR 전용 질의(pn any "KR")가 해외 출원인에게만 나가는지, 그 결과가 목록에
        # 더해지는지 확인한다. 스텁은 질의 문자열을 기록만 하고 문서를 돌려준다.
        seen_cql: list[str] = []

        class KrStub(Stub):
            def __call__(self, token, cql, start, end, timeout=None):
                seen_cql.append(cql)
                return super().__call__(token, cql, start, end, timeout)

        ps._search = KrStub(total=3, per_call=3)
        seen, collected = set(), []
        n = ps._collect_kr("stub-token", today, seen, collected)
        check(bool(seen_cql) and all('pn any "KR"' in q for q in seen_cql),
              "모든 질의가 KR 공개로 한정된다")
        kr_names = {a["name"] for a in cfg.APPLICANTS if a["region"] == "KR"}
        foreign = {a["name"] for a in cfg.APPLICANTS if a["region"] != "KR"}
        kr_terms = [a["q"] for a in cfg.APPLICANTS if a["region"] == "KR"]
        kr_terms = [t for q in kr_terms for t in ([q] if isinstance(q, str) else q)]
        check(not any(f'pa="{t}"' in q for q in seen_cql for t in kr_terms),
              f"국내 출원인({len(kr_names)}곳)에게는 질의하지 않는다")
        check(n > 0 and len(collected) == n, f"국내 공개를 목록에 더한다 ({n}건)")
        check(all(i.get("applicant") in foreign for i in collected),
              "더해진 항목은 전부 해외 출원인 것이다")
        per_ap: dict[str, int] = {}
        for i in collected:
            per_ap[i["applicant"]] = per_ap.get(i["applicant"], 0) + 1
        check(max(per_ap.values()) <= cfg.KR_LIMIT,
              f"출원인당 상한({cfg.KR_LIMIT})을 넘지 않는다")

        print("· 주간 수집 시작점 회전 (뒤쪽 출원인이 영구히 굶지 않는다)")
        n = len(cfg.APPLICANTS)
        starts, weeks = set(), [datetime(2026, 1, 5) + timedelta(weeks=w)
                                for w in range(12)]
        ok_seq = True
        for d in weeks:
            o = ps._collect_order(d)
            check_len = len(o) == n and {a["name"] for a in o} == \
                {a["name"] for a in cfg.APPLICANTS}
            if not check_len:
                ok_seq = False
                break
            starts.add(o[0]["name"])
            # seq 로 묶인 항목은 앞 항목보다 뒤에 있어야 한다
            pos = {a["name"]: i for i, a in enumerate(o)}
            for i, a in enumerate(cfg.APPLICANTS):
                if a.get("seq") and i > 0:
                    if pos[a["name"]] < pos[cfg.APPLICANTS[i - 1]["name"]]:
                        ok_seq = False
        check(check_len, "회전해도 출원인이 빠지거나 늘지 않는다")
        check(len(starts) >= 3, f"주마다 시작점이 바뀐다 (12주에 {len(starts)}가지)")
        check(ok_seq, "dedup 우선순위 묶음(seq)이 회전에 끊기지 않는다")
        # 한 실행이 소화하는 만큼(COLLECT_ROTATE) 씩 밀리면 몇 주 안에 전원이 선두권에 든다
        covered, span = set(), max(1, cfg.COLLECT_ROTATE)
        for d in weeks[:4]:
            covered |= {a["name"] for a in ps._collect_order(d)[:span]}
        check(len(covered) >= min(n, span * 3),
              f"4주 안에 {len(covered)}/{n}곳이 앞 {span}순위 안에 든다")

        print("· OPS 오류 사유 노출 (401 이 왜 났는지 로그에 남는다)")
        import io as _io
        import urllib.error as _ue

        class _Err(_ue.HTTPError):
            def __init__(self, code, body):
                super().__init__("u", code, "x", {}, _io.BytesIO(body.encode()))

        # 이 파일은 앞에서 _get_token·_search 를 스텁으로 바꿔 놓았다 → 진짜 함수를
        # 잠시 되돌려 놓고 검사한다(안 그러면 스텁을 시험하는 꼴이 된다).
        _orig_open = ps.urllib.request.urlopen
        _stub_search, _stub_token = ps._search, ps._get_token
        ps._search, ps._get_token = orig[0], orig[1]
        try:
            ps.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
                _Err(401, "<error><code>CLIENT.InvalidCredentials</code></error>"))
            try:
                ps._get_token()
                msg = ""
            except Exception as e:
                msg = str(e)
            check("CLIENT.InvalidCredentials" in msg,
                  "토큰 실패 시 EPO 가 준 사유가 메시지에 담긴다")
            ps.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
                _Err(403, "<fault><code>SERVER.QuotaPerHour</code></fault>"))
            try:
                ps._search("t", "q", 1, 25)
                msg2 = ""
            except Exception as e:
                msg2 = str(e)
                # 사유를 붙이면서 '403' 을 잃으면 쿼터 판정이 깨져 계속 두드리게 된다.
                check(ps._is_quota(e), "사유를 붙여도 쿼터(403) 판정이 유지된다")
            check("QuotaPerHour" in msg2, "검색 실패 사유도 메시지에 담긴다")
        finally:
            ps.urllib.request.urlopen = _orig_open
            ps._search, ps._get_token = _stub_search, _stub_token

        print("· 출원인 질의어 겹침 (총계 부풀림 방지)")
        # 목록은 공개번호 dedup 이 막아 주지만, 총계(stats)는 출원인별 독립 질의라
        # 막을 방법이 없다. 한 출원인의 검색 어구가 다른 출원인의 이름·어구에 통째로
        # 들어 있으면 그 회사 문서까지 세어 총계가 부풀려진다 — 실측으로 q="Siemens"
        # 가 Siemens Energy(53)·Gamesa(75) 를 삼켜 183 이 나왔다. 분야별 경쟁 구도의
        # 지분이 그만큼 틀어지므로 설정 단계에서 막는다.
        def _qs(a):
            q = a["q"]
            return [q] if isinstance(q, str) else list(q)

        overlaps = []
        for a in cfg.APPLICANTS:
            for b in cfg.APPLICANTS:
                if a is b:
                    continue
                for x in _qs(a):
                    for y in _qs(b) + [b["name"]]:
                        if x.lower() != y.lower() and x.lower() in y.lower():
                            overlaps.append(f'{a["name"]} q="{x}" ⊂ {b["name"]}("{y}")')
        for o in overlaps:
            print("    " + o)
        check(not overlaps,
              f"어떤 질의어도 다른 출원인을 삼키지 않는다 ({len(cfg.APPLICANTS)}곳)")

        print("· collect_offices (매일 공개국 집계 경로)")
        ps._search = Stub(total=5, per_call=1)
        st = ps.collect_offices(today)
        check(bool(st.get("totals")), f"총계 갱신 ({len(st.get('totals', {}))}곳)")
        check(bool(st.get("offices")), f"공개국 집계 갱신 ({len(st.get('offices', {}))}곳)")
        check(len(st.get("totals", {})) <= cfg.OFFICE_BATCH,
              f"하루 배치 상한({cfg.OFFICE_BATCH})을 넘지 않는다")

        print("· 집계 병합 (부분 실행이 기존 값을 지우지 않는다)")
        import patent_archive as pa
        store = {"totals": {"기존": 1}, "offices": {"기존": {"KR": 1}}, "updated": {}}
        pa.merge_stats(store, st, "2026-07-27")
        check(store["totals"].get("기존") == 1, "이전 출원인 값이 남는다")
        check(len(store["totals"]) > 1, "새 값이 더해진다")

        print("· 뉴스 아카이브 중복 판정·MOCK 표시")
        import news_archive as na
        # URL 동일 판정: 제목이 완전히 달라도 같은 기사면 다시 담기지 않아야 한다.
        days = {"2026-07-28": {"date": "2026-07-28", "articles": [
            {"title": "제목 A", "url": "https://ex.com/same"}]}}
        _, n = na.merge_today(days, "2026-07-29", [
            {"title": "완전히 딴판인 제목 B 입니다", "url": "https://ex.com/same"}], False)
        check(n == 0, "같은 URL 기사는 제목이 달라도 다시 담지 않는다")
        _, n = na.merge_today(days, "2026-07-29", [
            {"title": "아주 새로운 기사 제목 하나", "url": "https://ex.com/new"}], False)
        check(n == 1, "새 기사는 정상적으로 담긴다")
        # 하루 안에 라이브와 MOCK 이 섞여도 실데이터에 샘플 표시가 붙으면 안 된다.
        d2 = {}
        na.merge_today(d2, "2026-07-29", [{"title": "실데이터 기사", "url": "u1"}], False)
        na.merge_today(d2, "2026-07-29", [{"title": "샘플 기사", "url": "u2"}], True)
        arts = d2["2026-07-29"]["articles"]
        check(not arts[0].get("mock") and arts[1].get("mock") is True,
              "MOCK 표시는 항목별로 남는다")

        print("· 링크 스킴 제한 (javascript: 차단)")
        import re as _re
        import site_render as sr
        hrefs = _re.findall(r"href=\"'\+esc\((\w+)\(", sr._JS)
        raw = _re.findall(r"href=\"'\+esc\((?!safeUrl)", sr._JS)
        check("const safeUrl" in sr._JS, "safeUrl 헬퍼가 있다")
        check(bool(hrefs) and not raw,
              f"모든 링크 href 가 safeUrl 을 거친다 ({len(hrefs)}곳)")

        print("· 거래·지원 안내 데이터 (사람이 관리하는 상수)")
        import ip_guide as ig
        ln = ig.links()
        check(bool(ln) and all(l.get("label") and l.get("url") for l in ln),
              f"카드 링크에 이름·URL 이 다 있다 ({len(ln)}종)")
        check(all("{n}" in l["url"] for l in ln),
              "카드 링크 URL 에 공개번호 자리({n})가 있다")
        allitems = [i for g in ig.GUIDE for i in g["items"]]
        shown = [i for g in ig.guide() for i in g["items"]]
        check(bool(ig.guide()) and bool(shown),
              f"안내 항목이 있다 ({len(ig.guide())}묶음 {len(shown)}곳 표시"
              f" · 주소 미확인 {len(ig.pending())}곳)")
        check(all(i.get("name") and i.get("org") and i.get("what") for i in allitems),
              "뼈대 항목도 이름·기관·설명은 다 갖춘다")
        # 주소를 못 채운 항목이 화면으로 새면 안 된다 — 확인 안 된 링크를 기관
        # 사이트에 올리지 않으려고 url 유무로 거르는 구조다.
        check(all(i.get("url") for i in shown), "표시되는 항목은 전부 주소가 있다")
        check(len(shown) + len(ig.pending()) == len(allitems),
              "표시 + 미확인 = 전체 (조용히 사라지는 항목이 없다)")
        urls = [l["url"] for l in ln] + [i["url"] for i in shown]
        check(all(u.startswith("https://") for u in urls),
              f"모든 링크가 https 다 ({len(urls)}개)")
    finally:
        ps._search, ps._get_token = orig[0], orig[1]
        cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = orig[2], orig[3], orig[4]

    _kipris_checks()

    print(f"\n{'실패 ' + str(len(FAILS)) + '건' if FAILS else '전부 통과'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
