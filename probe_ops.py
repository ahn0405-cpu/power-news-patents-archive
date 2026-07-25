"""[임시 프로브] EPO OPS 인증 + 검색 응답 구조 실측.

OPS_KEY / OPS_SECRET (GitHub Secret) 로 OAuth 토큰을 받고, 몇 가지 CQL 검색을 실행해
응답 JSON 구조(발행국·제목·출원인·CPC 경로)와 쿼리 문법을 확인한다. 결과는 로그로만.
확인 후 이 파일과 워크플로는 삭제하고 정식 수집기로 대체한다.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"


def get_token(key: str, secret: str) -> str:
    cred = base64.b64encode(f"{key}:{secret}".encode()).decode()
    req = urllib.request.Request(
        AUTH_URL, data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + cred,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def search(token: str, cql: str, rng: str = "1-5") -> tuple[int, dict]:
    url = SEARCH_URL + "?q=" + urllib.parse.quote(cql)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "X-OPS-Range": rng})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status, json.loads(r.read())


def _walk_first_doc(data: dict):
    """응답에서 첫 exchange-document 를 최대한 찾아 반환(구조 확인용)."""
    try:
        wpd = data["ops:world-patent-data"]
        sr = wpd["ops:biblio-search"]["ops:search-result"]
        docs = sr.get("exchange-documents") or sr.get("ops:publication-reference")
        return sr, docs
    except Exception as e:
        return {"err": str(e)}, None


def main() -> None:
    key, secret = os.getenv("OPS_KEY", ""), os.getenv("OPS_SECRET", "")
    print("keys present:", bool(key), bool(secret))
    token = get_token(key, secret)
    print("token ok:", token[:12], "...")

    tests = [
        ('pa="Siemens"', "출원인 단독"),
        ('pa="Samsung Electronics" and pd within "20260101 20260731"', "출원인+발행일범위"),
        ('pa="State Grid" and cpc="H02J"', "출원인+CPC"),
        ('pa="Mitsubishi Electric" and ti="power semiconductor"', "출원인+제목"),
    ]
    for cql, label in tests:
        print(f"\n===== {label}: {cql} =====")
        try:
            status, data = search(token, cql)
        except Exception as e:
            print("  요청 실패:", e)
            continue
        sr, docs = _walk_first_doc(data)
        total = sr.get("@total-result-count") if isinstance(sr, dict) else "?"
        print("  status:", status, "| total-result-count:", total)
        # 첫 문서 구조 덤프(경로 파악용)
        first = None
        if isinstance(docs, list) and docs:
            first = docs[0]
        elif isinstance(docs, dict):
            first = docs
        if first is not None:
            s = json.dumps(first, ensure_ascii=False)
            print("  first doc keys:", list(first.keys()) if isinstance(first, dict) else type(first))
            print("  first doc (앞 1800자):", s[:1800])
        else:
            print("  docs 없음. search-result keys:", list(sr.keys()) if isinstance(sr, dict) else sr)


if __name__ == "__main__":
    main()
