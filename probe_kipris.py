"""KIPRISplus 탐색 — 발급받은 키로 **무엇이 열리는지**를 실측한다.

왜 프로브부터인가: KIPRISplus 는 서비스 단위로 신청·승인된다. 키 하나가 모든
서비스를 여는 게 아니라 승인된 서비스만 열린다. 어느 것이 승인됐는지 모르는 채
수집기를 짜면 매주 실패를 기다리게 된다(OPS 로 이미 겪었다).

개발 환경에서는 plus.kipris.or.kr 이 세션 프록시 허용목록에 없어 막힌다(실측:
`Host not in allowlist`). 러너는 외부 인터넷이 열려 있으므로 거기서 돌린다.

이 프로브가 답해야 할 세 가지 — 셋을 구분하지 못하면 진단이 안 된다:
  ① 러너에서 KIPRIS 서버에 닿는가            → 연결 실패 / 타임아웃
  ② 엔드포인트 **경로**가 맞는가              → 404 · 포털 HTML
  ③ 그 서비스가 이 키로 **승인**돼 있는가     → resultCode 인증·권한 오류

②의 함정(1차 실행에서 실제로 걸렸다): KIPRIS 는 없는 경로에 **HTTP 200 으로
한국어 404 안내 HTML** 을 돌려준다. 코드만 보고 판정하면 '열림'으로 읽혀 다섯
서비스가 열린 것처럼 나온다 — 전부 거짓이었다. 그래서 본문이 XML/JSON 인지까지
확인해야 판정이 선다.

사용(러너에서): KIPRIS_KEY=... python probe_kipris.py
  선택 입력(환경변수):
    KIPRIS_BASE      기준 경로 고정(비우면 후보를 훑어 사는 것을 찾는다)
    KIPRIS_SERVICE   특허·실용 서비스 이름 고정
    KIPRIS_PATHS     "서비스/오퍼레이션[?추가질의] ..." 공백 구분 — 직접 지정할 때
    KIPRIS_KEYPARAM  키 질의 이름 고정(기본: 후보를 차례로 시도)

키는 절대 파일에 적지 않는다. GitHub Secret → 환경변수로만 받는다.
"""
from __future__ import annotations

import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

KEY = os.getenv("KIPRIS_KEY", "").strip()
TIMEOUT = 25
HEAD = 900            # 응답 앞부분만 찍는다(로그가 길면 읽기 어렵다)

# KIPRISplus 는 세대마다 경로가 다르다. 1차 실행에서 /openapi/rest 아래 모든 경로가
# 포털 404 HTML 로 돌아왔다 → 그 계열은 죽었거나 이 키의 계열이 아니다. 신형
# (/kipo-api/kipi)까지 포함해 훑는다.
BASES = [
    "http://plus.kipris.or.kr/kipo-api/kipi",
    "https://plus.kipris.or.kr/kipo-api/kipi",
    "http://plus.kipris.or.kr/openapi/rest",
    "https://plus.kipris.or.kr/openapi/rest",
]
# 서비스 이름도 세대마다 다르고, KIPRIS 문서에 'Sevice' 오타 표기가 섞여 있다.
# 어느 쪽이 사는지는 실측으로만 갈린다.
SERVICES = [
    "patUtiModelInfoSearchSevice",
    "patUtiliInfoSearchSevice",
    "patUtiModelInfoSearchService",
    "patUtiliInfoSearchService",
]
# 키 질의 이름이 서비스 세대마다 다르다(accessKey / ServiceKey / serviceKey).
# 이름이 틀리면 '인증 실패'로 돌아와 승인 안 된 서비스와 구분되지 않는다 → 전부 시험.
KEY_PARAMS = ["accessKey", "ServiceKey", "serviceKey"]

# 경로 탐색에 쓸 대표 질의. 어느 세대에나 있는 오퍼레이션이라 기준으로 삼기 좋다.
DISCOVERY = ("getBibliographyDetailInfoSearch", {"applicationNumber": "1020200000001"})

# 승인 목록을 모르므로 '있을 법한 것'을 훑는다. {svc} 는 위에서 살아난 서비스 이름.
# 우리 화면에 필요한 것부터 순서를 잡았다:
#   서지 검색 → 특허 목록·분야 분류(지금 OPS 가 하는 일)
#   등록원부   → 권리변동(양도·이전) — EPO 에 없던 축, 거래 탭의 핵심 후보
#   해외특허   → 8대 분야 매트릭스를 지금처럼 전 세계 축으로 유지할 수 있는지
PROBES = [
    ("특허·실용 자유검색", "{svc}/getWordSearch",
     {"word": "전력변환", "numOfRows": "3", "pageNo": "1"}),
    ("특허·실용 항목검색(분류·출원인·기간)", "{svc}/getAdvancedSearch",
     {"inventionTitle": "전력", "patent": "true", "utility": "false",
      "numOfRows": "3", "pageNo": "1"}),
    # 우리 분야 분류(CATEGORIES)는 CPC 접두를 쓴다. 이 서비스가 IPC 만 받는다면
    # Y04S·Y02E(계량·스마트그리드)는 IPC 에 없는 코드라 통째로 빈다 → 여기서 확인.
    ("분류 검색 — IPC 로 되나 (H02M)", "{svc}/getAdvancedSearch",
     {"ipcNumber": "H02M", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("분류 검색 — CPC 전용 코드가 되나 (Y04S)", "{svc}/getAdvancedSearch",
     {"ipcNumber": "Y04S", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("출원인 검색 — 국내 공급자 찾기의 출발점", "{svc}/applicantNameSearchInfo",
     {"applicantName": "엘에스일렉트릭", "numOfRows": "3", "pageNo": "1"}),
    ("서지 상세(요약·청구항 포함 여부)", "{svc}/getBibliographyDetailInfoSearch",
     {"applicationNumber": "1020200000001"}),
    # 아래부터는 이름 확신이 낮다. 404 로 떨어지면 그건 '없는 이름'이라는 정보다.
    ("등록원부(권리변동·양도 이력)", "rgstRightInfoService/getRgstRightList",
     {"applicationNumber": "1020200000001"}),
    ("심사 진행 상태", "{svc}/getExamStatusInfoSearch",
     {"applicationNumber": "1020200000001"}),
    ("해외특허 — 미국", "UsPatentInfoSearchService/getWordSearch",
     {"word": "power converter", "numOfRows": "3", "pageNo": "1"}),
    ("해외특허 — 유럽", "EpPatentInfoSearchService/getWordSearch",
     {"word": "power converter", "numOfRows": "3", "pageNo": "1"}),
]


def _fetch(url: str) -> tuple[int, str, str]:
    """(HTTP 코드, 본문, 오류설명). 코드 0 은 연결 자체가 안 된 것."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "ip-power-probe/1.0",
        "Accept": "application/xml, application/json;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT,
                                    context=ssl.create_default_context()) as r:
            return r.getcode(), r.read().decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, ""
    except Exception as e:
        return 0, "", f"{type(e).__name__}: {e}"


def _tag(body: str, name: str) -> str:
    m = re.search(rf"<{name}>\s*(.*?)\s*</{name}>", body, re.S | re.I)
    return (m.group(1) if m else "").strip()


def _squash(body: str, n: int) -> str:
    return re.sub(r"\s+", " ", body).strip()[:n]


def _is_html(body: str) -> bool:
    """KIPRIS 는 없는 경로에 HTTP 200 + 한국어 안내 HTML 을 돌려준다(실측).
    코드만 보고 '열림'으로 찍으면 정반대 결론이 난다 → 본문으로 가른다."""
    head = body[:300].lstrip().lower()
    return head.startswith("<!doctype html") or head.startswith("<html") \
        or "페이지를 찾을 수 없습니다" in body[:2000]


def _verdict(code: int, body: str, err: str) -> tuple[str, str]:
    """(판정, 근거). ①연결 ②경로 ③승인 을 갈라 준다."""
    if code == 0:
        return "연결안됨", err
    # 개발 환경의 세션 프록시는 허용목록 밖 호스트에 403 을 돌려준다. 그대로 두면
    # '승인 안 된 서비스'로 읽혀 정반대의 결론이 난다 — KIPRIS 의 403 과 구분한다.
    if "not in allowlist" in body or "egress settings" in body:
        return "연결안됨", f"프록시가 막음(러너에서 돌려야 함) · {_squash(body, 90)}"
    if _is_html(body):
        return "경로없음", f"HTTP {code} · KIPRIS 포털 안내 HTML (그 경로가 없다)"
    rc, msg = _tag(body, "resultCode"), _tag(body, "resultMsg")
    up = body.upper()
    if code == 404 or "SERVICE ERROR" in up or "NO_OPENAPI_SERVICE" in up:
        return "경로없음", f"HTTP {code} · {msg or _squash(body, 100)}"
    # 파라미터 오류는 승인 문제가 아니다 — 서버가 우리 요청을 받아 해석까지 했다는
    # 뜻이라 오히려 '경로가 살아 있다'는 증거다. 2차 실행에서 이 둘을 뭉뚱그려
    # 열 개 전부 '미승인'으로 찍혔는데, 실제로는 질의 이름이 틀린 것이었다.
    if "INVALID_REQUEST_PARAMETER" in up or rc == "10":
        return "요청오류", f"HTTP {code} · resultCode={rc or '-'} {msg or _squash(body, 100)}"
    if code in (401, 403) or "SERVICE_KEY" in up or "SERVICE ACCESS DENIED" in up \
       or "미신청" in body or "권한" in body or (rc and rc not in ("00", "0")):
        return "미승인/키오류", f"HTTP {code} · resultCode={rc or '-'} {msg or _squash(body, 100)}"
    if code != 200:
        return "기타오류", f"HTTP {code} · {_squash(body, 100)}"
    return "열림", f"resultCode={rc or '-'} {msg or ''}".strip()


def _count(body: str) -> str:
    for t in ("totalCount", "count", "numOfRows"):
        v = _tag(body, t)
        if v:
            return f"{t}={v}"
    n = len(re.findall(r"<item[\s>]", body))
    return f"item={n}건" if n else "item 없음"


def _url(base: str, path: str, params: dict, kp: str) -> str:
    q = dict(params)
    q[kp] = KEY or "NO-KEY-PROBE"
    return f"{base}/{path}?" + urllib.parse.urlencode(q)


def _discover() -> tuple[str, str, str] | None:
    """(기준 경로, 서비스 이름, 키 질의 이름). 사는 조합을 실측으로 찾는다.

    1차 실행에서 /openapi/rest 계열이 전부 포털 HTML 로 돌아왔다 — 경로 자체가
    아니었다. 그래서 '경로 × 서비스이름 × 키질의' 를 훑어 살아 있는 조합을 먼저
    확정하고, 그 위에서 서비스별 승인 여부를 본다.
    """
    op, params = DISCOVERY
    fixed_base = (os.getenv("KIPRIS_BASE") or "").strip()
    fixed_svc = (os.getenv("KIPRIS_SERVICE") or "").strip()
    fixed_kp = (os.getenv("KIPRIS_KEYPARAM") or "").strip()
    bases = [fixed_base] if fixed_base else BASES
    svcs = [fixed_svc] if fixed_svc else SERVICES
    kps = [fixed_kp] if fixed_kp else KEY_PARAMS
    fallback = None
    for base in bases:
        for svc in svcs:
            for kp in kps:
                code, body, err = _fetch(_url(base, f"{svc}/{op}", params, kp))
                verdict, why = _verdict(code, body, err)
                print(f"  [{verdict:9s}] {base}/{svc}  (키질의={kp})")
                if verdict not in ("경로없음", "연결안됨"):
                    print(f"      {why}")
                if verdict == "열림":
                    return base, svc, kp
                # 서버가 우리 요청을 해석했다는 응답이면 '경로는 맞다'는 증거다.
                # 요청오류가 인증오류보다 더 앞선 신호다(파라미터만 고치면 된다).
                rank = {"요청오류": 2, "미승인/키오류": 1}.get(verdict, 0)
                if rank and (fallback is None or rank > fallback[0]):
                    fallback = (rank, base, svc, kp)
    return fallback[1:] if fallback else None


def _paths(svc: str) -> list[tuple[str, str, dict]]:
    raw = (os.getenv("KIPRIS_PATHS") or "").split()
    if raw:
        out = []
        for spec in raw:
            path, _, qs = spec.partition("?")
            out.append(("(직접 지정)", path, dict(urllib.parse.parse_qsl(qs))))
        return out
    return [(label, path.format(svc=svc), params) for label, path, params in PROBES]


def main() -> int:
    print("KIPRISplus 프로브 — 무엇이 열리는지 실측\n" + "=" * 66)
    keyless = not KEY
    print(f"키 길이: {len(KEY)}자" + ("  ← 0자면 시크릿이 전달되지 않은 것" if keyless else ""))
    if keyless:
        # 키가 없어도 ①연결 과 ②경로 는 답할 수 있다 — 오히려 그게 값싸다.
        print("KIPRIS_KEY 가 비어 있습니다 → ③승인여부는 판정하지 않고, "
              "①연결·②경로까지만 확인합니다.")

    print("\n①② 살아 있는 경로 찾기 — 경로 × 서비스이름 × 키질의")
    found = _discover()
    if not found:
        print("\n  → 어떤 조합도 응답하지 않았습니다. KIPRISplus 마이페이지의 "
              "API 명세에 적힌 '요청 URL' 샘플을 알려주시면 그 경로로 고정하겠습니다.")
        return 2
    base, svc, kp = found
    print(f"\n  → 기준: {base}/{svc}  (키질의={kp})")

    # 키 질의 이름이 틀리면 서버는 '필수 파라미터 없음'으로 답한다 — 파라미터가
    # 틀린 것과 응답이 똑같아 구분되지 않는다. 같은 요청에 이름만 바꿔 나란히
    # 찍어 두면 어느 쪽이 원인인지 눈으로 갈린다(응답 XML 이 짧아 값싸다).
    op, dparams = DISCOVERY
    print("\n   키 질의 이름 비교 — 같은 요청, 이름만 바꿔서")
    for cand in KEY_PARAMS:
        code, body, err = _fetch(_url(base, f"{svc}/{op}", dparams, cand))
        v, why = _verdict(code, body, err)
        print(f"     · {cand:11s} [{v}] {why}")
    # 키를 아예 빼면 '키가 없을 때의 오류'가 무엇인지 알 수 있다 → 위 셋과 비교해
    # 우리가 보낸 키가 인식은 되고 있는지 판단할 근거가 된다.
    nokey = f"{base}/{svc}/{op}?" + urllib.parse.urlencode(dparams)
    code, body, err = _fetch(nokey)
    v, why = _verdict(code, body, err)
    print(f"     · (키 없음)   [{v}] {why}")

    rows: list[tuple[str, str, str, str]] = []
    print("\n③ 서비스별 — 이 키로 승인돼 있는가")
    for label, path, params in _paths(svc):
        code, body, err = _fetch(_url(base, path, params, kp))
        verdict, why = _verdict(code, body, err)
        # 키가 없을 때의 '인증 오류'는 실패가 아니라 **경로가 맞다는 증거**다.
        if keyless and verdict == "미승인/키오류":
            verdict = "경로확인"
        extra = _count(body) if verdict == "열림" else ""
        mark = {"열림": "✅", "경로확인": "🔎", "요청오류": "🛠️", "미승인/키오류": "🔒",
                "경로없음": "❓", "연결안됨": "⛔", "기타오류": "⚠️"}.get(verdict, "·")
        print(f"\n {mark} [{verdict}] {label}")
        print(f"    {path}")
        print(f"    {why}" + (f" · {extra}" if extra else ""))
        # 응답 본문은 판정과 무관하게 찍는다. 오류 XML 도 짧고, 거기에 '어느
        # 파라미터가 문제인지'가 적혀 오는 경우가 있어 다음 수를 정하는 근거가 된다.
        if verdict != "경로없음":
            print("    ── 응답 앞부분 " + "-" * 40)
            print("    " + _squash(body, HEAD))
        rows.append((mark, verdict, label, path))

    print("\n" + "=" * 66)
    print("요약")
    for mark, verdict, label, path in rows:
        print(f"  {mark} {verdict:12s} {label}  [{path}]")
    ok = [r for r in rows if r[1] == "열림"]
    print(f"\n열린 서비스 {len(ok)}개 / 시험 {len(rows)}개 · "
          f"기준 {base}/{svc} (키질의={kp})")
    print("판정 읽는 법:  ✅ 쓸 수 있다 · 🔎 경로는 맞다(키 없이 확인) · "
          "🛠️ 경로는 살아 있고 우리 요청이 틀렸다(파라미터 교정) · "
          "🔒 경로는 맞고 승인이 없다(추가 신청) · ❓ 그 경로가 없다 · "
          "⛔ 연결 자체가 안 된다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
