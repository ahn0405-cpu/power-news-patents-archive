"""[임시 프로브] Google Patents 무키 xhr 엔드포인트 — 출원인(assignee) 검색 실측.

목적(2가지):
  1) 출원인으로 조회하는 질의 문법이 무키로 동작하는가? (여러 후보 형태를 비교)
  2) 검색 결과 항목에 CPC(분류코드)가 포함되는가? → 분야 분류를 CPC로 할지 판단.

Actions 러너에서만 의미 있음(사내/샌드박스는 patents.google.com 차단).
결과는 로그로 출력만 한다. 확인 후 이 파일과 워크플로는 삭제한다.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_BASE = "https://patents.google.com/xhr/query"


def _fetch_raw(inner: str) -> dict:
    url = f"{_BASE}?url={urllib.parse.quote(inner, safe='')}&exp="
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "Referer": "https://patents.google.com/",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace").lstrip()
    if text.startswith(")]}'"):
        text = text.split("\n", 1)[-1]
    return json.loads(text)


def _summarize(label: str, inner: str) -> None:
    print(f"\n===== {label} =====")
    print(f"  inner q: {inner}")
    try:
        data = _fetch_raw(inner)
    except Exception as e:
        print(f"  !! 요청 실패: {e}")
        return
    results = data.get("results", {}) or {}
    total = results.get("total_num_results")
    clusters = results.get("cluster", []) or []
    rows = []
    for cl in clusters:
        for res in cl.get("result", []) or []:
            rows.append(res)
    print(f"  total_num_results: {total} · 파싱된 결과 수: {len(rows)}")
    if not rows:
        print("  (결과 없음)")
        return
    # 첫 결과의 구조(어떤 필드가 오는지) 덤프 — CPC 여부 확인
    first = rows[0]
    print(f"  result[0] keys: {sorted(first.keys())}")
    pat = first.get("patent", {}) or {}
    print(f"  result[0].patent keys: {sorted(pat.keys())}")
    # CPC/분류 관련 필드 탐색
    cpc_like = {k: pat[k] for k in pat
                if any(t in k.lower() for t in ("cpc", "class", "ipc"))}
    print(f"  분류(CPC/IPC/class) 관련 필드: {cpc_like if cpc_like else '없음'}")
    # 상위 3건 제목·출원인
    for res in rows[:3]:
        p = res.get("patent", {}) or {}
        print(f"    - [{p.get('publication_number','?')}] "
              f"{(p.get('title') or '')[:55]} / 출원인: {p.get('assignee','')}")


def main() -> None:
    # (라벨, 내부 질의) — 한국·미국 출원인, 여러 문법 후보 비교
    cases = [
        # 전용 assignee 파라미터 (공백 포함 이름)
        ("US · assignee= (General Electric)",
         'assignee=General Electric&country=US&sort=new'),
        ("KR · assignee= (Samsung Electronics)",
         'assignee=Samsung Electronics&country=KR&sort=new'),
        ("KR · assignee= (한글: 한국전력공사)",
         'assignee=한국전력공사&country=KR&sort=new'),
        # q= 안의 assignee: 연산자 (scholar 스타일)
        ("US · q=assignee:\"General Electric\"",
         'q=assignee:"General Electric"&country=US&sort=new'),
        # 그냥 자유어 q= (참고: 정밀도 낮음)
        ("US · q=General Electric (자유어)",
         'q=General Electric&country=US&sort=new'),
        # 출원인 + 분야 키워드 결합 가능성(교집합) — TI 와 assignee 동시
        ("KR · assignee=+TI 결합 (삼성 & 전력반도체)",
         'assignee=Samsung Electronics&q=TI="power semiconductor"&country=KR&sort=new'),
    ]
    for label, inner in cases:
        _summarize(label, inner)


if __name__ == "__main__":
    main()
