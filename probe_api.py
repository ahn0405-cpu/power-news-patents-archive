"""공공데이터포털(data.go.kr) 오픈API 탐색 도구 — 응답 구조를 눈으로 확인한다.

왜 필요한가: 개발 환경에서 data.go.kr 접속이 막혀 있어 오퍼레이션 이름과 응답
필드를 확인할 수 없다. GitHub Actions 러너는 외부 인터넷이 열려 있으므로 거기서
한 번 돌려 로그로 구조를 본 뒤 수집기를 짠다(추측으로 짜고 매주 기다리지 않는다).

사용: DATA_GO_KR_KEY=... [PROBE_ENDPOINT=...] [PROBE_OPS="a b c"] python probe_api.py
      (비우면 아래 기본값 — 특허기술거래 국유판매기술정보)

키는 절대 파일에 적지 않는다. GitHub Secret → 환경변수로만 받는다.
"""
from __future__ import annotations

import os
import ssl
import sys
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = "https://apis.data.go.kr/1431000/StaownTradePatentInfoService"
# 지식재산처 '특허기술거래 국유판매기술정보' 의 상세기능(활용신청 승인 목록 기준).
# 무상(Free) 쪽이 먼저다 — 국유특허 중 무상 실시가 가능한 기술은 중소기업이 돈을
#들이지 않고 쓸 수 있는 목록이라, 거래 정보보다 실용적이다.
DEFAULT_OPS = [   # 연결이 막히면 앞의 한두 개만 봐도 판정된다
    "getFreeTL",          # 무상 · 발명의 명칭 리스트
    "getFreePatentee",    # 무상 · 권리자 리스트
    "getPayTL",           # 유상 · 발명의 명칭 리스트
    "getPayPatentee",     # 유상 · 권리자 리스트
    "getDateList",        # 날짜 리스트
]
TIMEOUT = 25
HEAD = 1400          # 응답 앞부분만 찍는다(로그가 길면 읽기 어렵다)


def _fetch(url: str) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode(
                "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(
            "utf-8", "replace")
    except Exception as e:
        return 0, "", f"({type(e).__name__}) {e}"


def _try(endpoint: str, op: str, key: str, label: str) -> bool:
    # serviceKey 는 이미 URL 인코딩된 형태로 발급되기도 한다 → 다시 인코딩하면
    # 깨진다. 그래서 쿼리를 손으로 붙이고, 인코딩·디코딩 두 형태를 다 시도한다.
    url = (f"{endpoint}/{op}?serviceKey={key}"
           f"&pageNo=1&numOfRows=3&type=xml&_type=xml")
    status, ctype, body = _fetch(url)
    ok = "<resultCode>00</resultCode>" in body or '"resultCode":"00"' in body
    print(f"\n── {op}  [{label} 키]  HTTP {status}  {ctype}")
    print(("  ✅ 정상 응답" if ok else "  ·  아님") + f" (본문 {len(body)}자)")
    print("  " + body[:HEAD].replace("\n", "\n  "))
    return ok


def _connectivity(endpoint: str) -> None:
    """어디서 막히는지 단계별로 확인 — DNS / TCP / TLS / 애플리케이션.

    타임아웃만 보면 '서버가 느린 건지, 아예 못 닿는 건지, 해외 IP 를 막는 건지'
    구분이 안 된다. 국내 공공 API 는 해외 IP 를 막아 두는 경우가 있어, 러너에서
    쓸 수 있는지 자체가 설계 판단(자동화 가능 여부)에 직결된다.
    """
    import socket
    host = urllib.parse.urlparse(endpoint).hostname or ""
    print(f"\n[연결 진단] host={host}")
    try:
        infos = socket.getaddrinfo(host, None)
        ips = sorted({i[4][0] for i in infos})
        print(f"  DNS  ✅ {', '.join(ips)}")
    except Exception as e:
        print(f"  DNS  ❌ {type(e).__name__}: {e}")
        return
    for port in (443, 80):
        s = socket.socket()
        s.settimeout(10)
        try:
            s.connect((ips[0], port))
            print(f"  TCP {port} ✅ 연결됨")
        except Exception as e:
            print(f"  TCP {port} ❌ {type(e).__name__}: {e}")
        finally:
            s.close()


def main() -> int:
    key = os.getenv("DATA_GO_KR_KEY", "").strip()
    if not key:
        print("DATA_GO_KR_KEY 가 비어 있습니다(GitHub Secret 확인).")
        return 1
    # 위치 인자는 쓰지 않는다 — 앞 인자를 비우면 뒤 인자가 앞자리로 밀려 들어간다
    # (실측: endpoint 를 비우고 ops 만 넘겼더니 ops 가 엔드포인트로 해석됐다).
    endpoint = (os.getenv("PROBE_ENDPOINT") or "").strip() or DEFAULT_ENDPOINT
    ops = (os.getenv("PROBE_OPS") or "").split() or DEFAULT_OPS
    print(f"엔드포인트: {endpoint}")
    print(f"오퍼레이션 후보 {len(ops)}개: {', '.join(ops)}")
    print(f"키 길이 {len(key)}자 · '%' 포함: {'%' in key}")
    _connectivity(endpoint)

    variants = [("인코딩", key)]
    dec = urllib.parse.unquote(key)
    if dec != key:
        variants.append(("디코딩", urllib.parse.quote(dec, safe="")))
    # https 가 막혀도 http 는 열려 있는 서비스가 있다(공공 API 에 드물지 않다).
    schemes = [endpoint]
    if endpoint.startswith("https://"):
        schemes.append("http://" + endpoint[len("https://"):])

    hit = []
    for op in ops:
        done = False
        for base in schemes:
            for label, k in variants:
                tag = label + ("/http" if base.startswith("http://") else "")
                if _try(base, op, k, tag):
                    hit.append((op, tag))
                    done = True
                    break        # 한 형태가 되면 다른 형태는 볼 필요 없다
            if done:
                break
    print("\n" + "=" * 60)
    print("정상 응답:", ", ".join(f"{o}({l})" for o, l in hit) or "없음")
    if not hit:
        print("→ 오퍼레이션 이름이 후보에 없을 수 있습니다. 활용가이드 문서의 "
              "'상세기능 목록' 에 있는 이름을 인자로 넘겨 다시 돌리세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
