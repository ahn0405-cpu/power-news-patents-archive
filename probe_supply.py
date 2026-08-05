"""국내 공급자 탐색 — 출원인을 정해 놓지 않고 '누가 나오는지' 본다.

왜 필요한가: 지금 수집은 큐레이션한 출원인 65곳 × 전력 CPC 다. 그 65곳은 전부
대기업·해외 대형사라 거래에서 **수요자이자 경쟁자**이고, 정작 팔 물건을 가진 쪽
(대학 산학협력단·출연연·국내 중소기업)은 표본에 거의 없다. 실측으로 데이터센터
분야는 국내 지분 0% 로 나오는데, 그게 '한국에 아무도 없다' 는 뜻인지 '우리가 안
보고 있다' 는 뜻인지 지금 데이터로는 구분되지 않는다.

목록을 늘려서는 풀리지 않는다 — 이름을 모르고, OPS 쿼터도 이미 병목이다.
반대로 **분야(CPC) + 국내 공개**로 찾고 출원인을 사후에 모으면, 우리가 이름조차
모르는 곳이 스스로 드러난다. 비용도 분야당 1~2요청이라 훨씬 싸다.

이 스크립트는 만들기 전 확인용이다. 무엇이 얼마나 나오는지 보고 설계한다.

사용(러너에서): OPS_KEY/OPS_SECRET 환경변수 + python probe_supply.py
"""
from __future__ import annotations

import collections
import os
import re
import sys
from datetime import datetime, timedelta

import patent_config as cfg
import patent_source as ps

# 워크플로 입력이 비어 있으면 빈 문자열로 온다 → int("") 로 터지지 않게 한다.
PER_CAT = int((os.getenv("SUPPLY_PER_CAT") or "").strip() or 50)  # 분야당 훑을 문서 수
TOP = 12                                            # 분야별로 찍을 출원인 수

# 이름만 보고도 성격이 갈린다. 대학·출연연은 기술이전 전담조직이 있어 거래가
# 실제로 이뤄지는 곳이고, 공사·공단은 공공 수요처다. 나머지는 기업으로 둔다.
KINDS = [
    ("대학", re.compile(r"산학협력단|UNIV|대학교|대학")),
    ("출연연·연구기관",
     re.compile(r"연구원|연구소|RESEARCH|INSTITUTE|KIST|ETRI|KAERI|ACADEMY")),
    ("공사·공단", re.compile(r"공사|공단|POWER CORP|KEPCO")),
]


def _kind(name: str) -> str:
    up = (name or "").upper()
    for label, rx in KINDS:
        if rx.search(name or "") or rx.search(up):
            return label
    return "기업"


def _cql_domestic(cat: dict, days: int, today: datetime) -> str:
    """출원인 조건 없이 '이 분야 + 국내 공개' 만. 누가 나오는지가 목적이다."""
    end = today.date()
    start = end - timedelta(days=days)
    cpc_or = " or ".join(f'cpc="{c}"' for c in cat["cpc"])
    return (f'({cpc_or}) and pd within '
            f'"{start.strftime("%Y%m%d")} {end.strftime("%Y%m%d")}" '
            f'and pn any "KR"')


def main() -> int:
    if not (cfg.OPS_KEY and cfg.OPS_SECRET):
        print("OPS_KEY/OPS_SECRET 이 없습니다.")
        return 1
    today = datetime.now()
    token = ps._get_token()
    known = {a["name"] for a in cfg.APPLICANTS}
    grand: collections.Counter = collections.Counter()
    kinds: collections.Counter = collections.Counter()
    calls = 0

    for cat in cfg.CATEGORIES:
        cql = _cql_domestic(cat, cfg.LOOKBACK_DAYS, today)
        names: collections.Counter = collections.Counter()
        total = 0
        start = 1
        while start <= PER_CAT:
            end = min(start + 24, PER_CAT)
            try:
                data, total = ps._search(token, cql, start, end)
                calls += 1
            except Exception as e:
                print(f"  ! [{cat['name']}] {e}")
                break
            docs = ps._docs(data)
            if not docs:
                break
            for d in docs:
                it = ps._normalize(d)
                if not it:
                    continue
                nm = (it.get("assignee") or "").strip()
                if nm:
                    names[nm] += 1
            if len(docs) < (end - start + 1):
                break
            start = end + 1
        print(f"\n── {cat['emoji']} {cat['name']} · 국내 공개 전체 {total}건 "
              f"(상위 {sum(names.values())}건에서 출원인 {len(names)}곳)")
        for nm, c in names.most_common(TOP):
            k = _kind(nm)
            mark = "  " if any(x.lower() in nm.lower() for x in known) else "★"
            print(f"   {mark} [{k:8s}] {nm[:44]:46s} {c}")
            grand[nm] += c
            kinds[k] += c

    print("\n" + "=" * 66)
    print(f"요청 {calls}회 · 서로 다른 출원인 {len(grand)}곳")
    print("성격별 문서 수:", dict(kinds))
    print("\n★ = 지금 추적 목록에 없는 곳 — 상위 25")
    shown = 0
    for nm, c in grand.most_common():
        if any(x.lower() in nm.lower() for x in known):
            continue
        print(f"   [{_kind(nm):8s}] {nm[:50]:52s} {c}")
        shown += 1
        if shown >= 25:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
