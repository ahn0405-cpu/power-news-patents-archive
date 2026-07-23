"""[임시] Google Patents xhr 검색 문법 프로브.

멀티 CPC(cpc=A,B)·필드연산자(TI=/CL=) 지원 여부와 재현율(total_num_results)을
실측하기 위한 일회용 스크립트. 결과 보고 최적 검색식을 확정한 뒤 삭제한다.
"""
from __future__ import annotations
import html
import json
import re
import urllib.parse
import urllib.request

BASE = "https://patents.google.com/xhr/query"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def run(label: str, inner: str) -> None:
    url = f"{BASE}?url={urllib.parse.quote(inner, safe='')}&exp="
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Referer": "https://patents.google.com/"})
    try:
        raw = urllib.request.urlopen(req, timeout=25).read()
        t = raw.decode("utf-8", "replace").lstrip()
        if t.startswith(")]}'"):
            t = t.split("\n", 1)[-1]
        d = json.loads(t)
        res = d.get("results", {}) or {}
        tot = res.get("total_num_results")
        titles = []
        for cl in res.get("cluster", []) or []:
            for r in cl.get("result", []) or []:
                pt = (r.get("patent", {}) or {}).get("title", "")
                pt = html.unescape(re.sub(r"<[^>]+>", "", pt)).strip()
                if pt:
                    titles.append(pt[:38])
        print(f"[{label}] total={tot} shown={len(titles)} :: " + "  |  ".join(titles[:2]))
    except Exception as e:
        print(f"[{label}] ERROR {type(e).__name__}: {e}")


CASES = [
    # 멀티 CPC / no-cpc 비교 (재생·저장: ESS)
    ("KR ESS no-cpc",        'q="에너지저장장치"&country=KR&sort=new'),
    ("KR ESS cpc=H01M",      'q="에너지저장장치"&country=KR&cpc=H01M&sort=new'),
    ("KR ESS cpc=H02J",      'q="에너지저장장치"&country=KR&cpc=H02J&sort=new'),
    ("KR ESS cpc=H01M,H02J", 'q="에너지저장장치"&country=KR&cpc=H01M,H02J&sort=new'),
    # 필드 연산자(제목/청구항) 문법 후보들
    ("KR ESS TI=\"\"",       'q=TI="에너지저장장치"&country=KR&sort=new'),
    ("KR ESS TI=()",         'q=TI=(에너지저장장치)&country=KR&sort=new'),
    ("KR ESS intitle:",      'q=intitle:에너지저장장치&country=KR&sort=new'),
    ("KR ESS CL=\"\"",       'q=CL="에너지저장장치"&country=KR&sort=new'),
    # 전력반도체: 소자(H01L) vs 변환회로(H02M) vs 멀티
    ("US pwr-semi cpc=H02M", 'q="power semiconductor"&country=US&cpc=H02M&sort=new'),
    ("US pwr-semi cpc=H01L", 'q="power semiconductor"&country=US&cpc=H01L&sort=new'),
    ("US pwr-semi H02M,H01L",'q="power semiconductor"&country=US&cpc=H02M,H01L&sort=new'),
    ("US pwr-semi TI=",      'q=TI="power semiconductor"&country=US&sort=new'),
    # grid 세분화 확인
    ("KR 송전 cpc=H02",       'q="송전"&country=KR&cpc=H02&sort=new'),
    ("KR 송전 cpc=H02J,H02G", 'q="송전"&country=KR&cpc=H02J,H02G&sort=new'),
]

if __name__ == "__main__":
    for lbl, inner in CASES:
        run(lbl, inner)
