"""수집기 스모크 테스트 — 네트워크·키 없이 '라이브 경로'를 실제로 실행해 본다.

왜 필요한가: MOCK 빌드는 _mock_collect 만 타고, 매일 도는 뉴스 실행은
collect_offices 만 탄다 → OPS 를 쓰는 _live_collect 는 주 1회 특허 워크플로에서만
실행돼, 거기 있는 오류(예: 미정의 변수)가 월요일에야 드러난다. py_compile 로는
NameError 를 못 잡으므로, OPS 응답을 흉내 내는 스텁을 끼워 두 경로를 다 돌려본다.

사용: python selftest.py   (의존성 없음. 실패하면 exit 1)
"""
from __future__ import annotations

import sys
from datetime import datetime

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
    finally:
        ps._search, ps._get_token = orig[0], orig[1]
        cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = orig[2], orig[3], orig[4]

    print(f"\n{'실패 ' + str(len(FAILS)) + '건' if FAILS else '전부 통과'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
