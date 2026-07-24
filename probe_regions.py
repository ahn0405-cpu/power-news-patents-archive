"""[임시 프로브] 중국·일본·유럽 주요 출원인이 무키 검색에 잡히는지 실측.

목적:
  1) 각 후보 출원인 이름(assignee=)이 Google Patents 색인과 매칭되는가?
  2) country 제한 없이(전 세계 공개) assignee+TI 로 조회하면 영어 제목으로 잡히는가?
     → 비영어권(중/일/유럽) 특허도 Google 영문 제목으로 색인되는지 확인.
결과는 로그로만. 확인 후 이 파일과 워크플로는 삭제.
"""
from __future__ import annotations
import json, urllib.parse, urllib.request, time

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_BASE = "https://patents.google.com/xhr/query"


def _fetch(inner: str) -> dict:
    url = f"{_BASE}?url={urllib.parse.quote(inner, safe='')}&exp="
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "Referer": "https://patents.google.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    t = raw.decode("utf-8", "replace").lstrip()
    if t.startswith(")]}'"):
        t = t.split("\n", 1)[-1]
    return json.loads(t)


def probe(name: str, q: str, term: str) -> None:
    # country 제한 없이 전 세계 공개에서 assignee+TI 조회
    inner = f'assignee={q}&q=TI="{term}"&sort=new'
    try:
        data = _fetch(inner)
    except Exception as e:
        print(f"  {name:<22} FAIL: {e}"); return
    res = (data.get("results", {}) or {})
    total = res.get("total_num_results")
    rows = [r for cl in res.get("cluster", []) or [] for r in cl.get("result", []) or []]
    samp = []
    for r in rows[:2]:
        p = r.get("patent", {}) or {}
        num = p.get("publication_number", "?")
        samp.append(f"[{num[:2]}] {(p.get('title') or '')[:42]} / {(p.get('assignee') or '')[:22]}")
    print(f"  {name:<22} total={total} got={len(rows)}  term='{term}'")
    for s in samp:
        print(f"      {s}")
    time.sleep(0.4)


def main() -> None:
    print("=== 중국 (CN) ===")
    for n, q, t in [
        ("State Grid", "State Grid Corporation of China", "power transmission"),
        ("China Southern Grid", "China Southern Power Grid", "power transmission"),
        ("CATL", "Contemporary Amperex Technology", "energy storage"),
        ("BYD", "BYD", "energy storage"),
        ("Huawei", "Huawei", "power converter"),
        ("Sungrow", "Sungrow Power Supply", "photovoltaic"),
        ("TBEA", "TBEA", "power transmission"),
        ("CGN", "China General Nuclear", "nuclear reactor"),
    ]:
        probe(n, q, t)
    print("=== 일본 (JP) ===")
    for n, q, t in [
        ("Hitachi Energy", "Hitachi Energy", "power transmission"),
        ("Hitachi", "Hitachi", "power transmission"),
        ("Mitsubishi Electric", "Mitsubishi Electric", "power semiconductor"),
        ("Mitsubishi Heavy", "Mitsubishi Heavy Industries", "nuclear reactor"),
        ("Toshiba", "Toshiba", "nuclear reactor"),
        ("Panasonic", "Panasonic", "energy storage"),
        ("Fuji Electric", "Fuji Electric", "power semiconductor"),
        ("Sumitomo Electric", "Sumitomo Electric", "power transmission"),
    ]:
        probe(n, q, t)
    print("=== 유럽 (EU) ===")
    for n, q, t in [
        ("Siemens Energy", "Siemens Energy", "power transmission"),
        ("Siemens", "Siemens", "switchgear"),
        ("ABB", "ABB", "switchgear"),
        ("Schneider Electric", "Schneider Electric", "switchgear"),
        ("Prysmian", "Prysmian", "power transmission"),
        ("Vestas", "Vestas Wind Systems", "wind turbine"),
        ("Framatome", "Framatome", "nuclear reactor"),
        ("Nexans", "Nexans", "power transmission"),
    ]:
        probe(n, q, t)


if __name__ == "__main__":
    main()
