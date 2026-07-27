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
    finally:
        ps._search, ps._get_token = orig[0], orig[1]
        cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = orig[2], orig[3], orig[4]

    print(f"\n{'실패 ' + str(len(FAILS)) + '건' if FAILS else '전부 통과'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
