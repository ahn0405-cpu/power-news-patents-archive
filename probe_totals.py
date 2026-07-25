"""[임시 프로브] EPO OPS — 전체 건수(total-result-count) 확보 + 쿼터/스로틀 실측.

확인 목적:
  1) 검색 응답 어디에 '조건에 맞는 전체 건수'가 오는가? (범위를 1-1 로 줄여도 오는가)
  2) 1-1 요청(1건만 받기)이 쿼터를 얼마나 쓰는가 → 출원인×분야 count 쿼리(248회)가 현실적인가
  3) OPS 스로틀 헤더(X-Throttling-Control 등)가 무엇을 알려주는가

확인 후 이 파일과 워크플로는 삭제한다.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request

import patent_config as cfg

AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"


def _token() -> str:
    cred = base64.b64encode(f"{cfg.OPS_KEY}:{cfg.OPS_SECRET}".encode()).decode()
    req = urllib.request.Request(
        AUTH_URL, data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + cred,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())["access_token"]


def _search(token: str, cql: str, rng: str):
    url = SEARCH_URL + "?q=" + urllib.parse.quote(cql)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "X-OPS-Range": rng})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read()), dict(r.headers)


def _cql(pa: str, cpc_list=None) -> str:
    base = f'pa="{pa}" and pd within "20260427 20260723"'
    if cpc_list:
        base += " and (" + " or ".join(f'cpc="{c}"' for c in cpc_list) + ")"
    else:
        allc = [c for cat in cfg.CATEGORIES for c in cat["cpc"]]
        base += " and (" + " or ".join(f'cpc="{c}"' for c in allc) + ")"
    return base


def _find_total(obj, path=""):
    """응답에서 'total' 이 들어간 키를 전부 찾아 (경로, 값) 으로."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if "total" in k.lower() and not isinstance(v, (dict, list)):
                hits.append((p, v))
            hits += _find_total(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):
            hits += _find_total(v, f"{path}[{i}]")
    return hits


def main() -> None:
    tk = _token()
    print("토큰 발급 OK\n")

    print("=== 1) 범위 1-1 로도 전체 건수가 오는가 ===")
    for name in ["State Grid Corporation of China", "Samsung Electronics", "Eaton"]:
        try:
            data, hdrs = _search(tk, _cql(name), "1-1")
        except Exception as e:
            print(f"  {name:<34} 실패: {e}"); continue
        tot = _find_total(data)
        docs = data.get("ops:world-patent-data", {}).get("ops:biblio-search", {}) \
                   .get("ops:search-result", {}).get("exchange-documents")
        n = 1 if isinstance(docs, dict) else (len(docs) if isinstance(docs, list) else 0)
        print(f"  {name:<34} total 필드={tot} · 받은 문서={n}")
        print(f"      throttle: {hdrs.get('X-Throttling-Control','(없음)')}")
        time.sleep(0.5)

    print("\n=== 2) 같은 질의를 1-25 로 했을 때와 비교 ===")
    data, hdrs = _search(tk, _cql("Samsung Electronics"), "1-25")
    print("  1-25 total 필드:", _find_total(data))
    print("  throttle:", hdrs.get("X-Throttling-Control", "(없음)"))

    print("\n=== 3) 출원인×분야 count 쿼리(1-1) 8회 — 매트릭스 정확도용 ===")
    t0 = time.time()
    for cat in cfg.CATEGORIES:
        try:
            data, hdrs = _search(tk, _cql("Samsung Electronics", cat["cpc"]), "1-1")
            tot = _find_total(data)
            val = tot[0][1] if tot else "?"
        except Exception as e:
            val = f"실패({e})"
        print(f"  {cat['emoji']} {cat['name']:<20} → {val}")
        time.sleep(0.4)
    print(f"  8회 소요: {time.time()-t0:.1f}s · 31곳 환산 ≈ {(time.time()-t0)*31/60:.1f}분")
    print("  마지막 throttle:", hdrs.get("X-Throttling-Control", "(없음)"))


if __name__ == "__main__":
    main()
