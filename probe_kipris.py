"""KIPRISplus 탐색 — 발급받은 키로 **무엇이 열리는지**를 실측한다.

왜 프로브부터인가: KIPRISplus 는 서비스 단위로 신청·승인된다. 키 하나가 모든
서비스를 여는 게 아니라, 승인된 서비스만 열린다. 그런데 우리는 어느 서비스가
승인됐는지 로그로 확인한 적이 없다 — 추측으로 수집기를 짜면 매주 실패를 기다리게
된다(OPS 로 이미 겪었다).

개발 환경에서는 plus.kipris.or.kr 이 세션 프록시 허용목록에 없어 막힌다(실측:
`Host not in allowlist`). 러너는 외부 인터넷이 열려 있으므로 거기서 한 번 돌린다.

이 프로브가 답해야 할 세 가지 — 셋을 구분하지 못하면 진단이 안 된다:
  ① 러너에서 KIPRIS 서버에 닿는가            → 연결 실패 / 타임아웃
  ② 서비스 **이름**이 맞는가                  → HTTP 404 · "SERVICE ERROR"
  ③ 그 서비스가 이 키로 **승인**돼 있는가     → resultCode 인증·권한 오류

사용(러너에서): KIPRIS_KEY=... python probe_kipris.py
  선택 입력(환경변수):
    KIPRIS_PATHS  "서비스/오퍼레이션[?추가질의] ..." 공백 구분 — 직접 지정할 때
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

# KIPRISplus 레거시 REST 는 http, 신형은 https 로도 받는다. 어느 쪽이 사는지 모르니
# 첫 요청에서 둘 다 시험하고, 사는 쪽을 나머지 요청에 그대로 쓴다.
BASES = [
    "http://plus.kipris.or.kr/openapi/rest",
    "https://plus.kipris.or.kr/openapi/rest",
]
# 키 질의 이름이 서비스 세대마다 다르다(accessKey / ServiceKey / serviceKey).
# 이름이 틀리면 '인증 실패'로 돌아와 승인 안 된 서비스와 구분되지 않는다 → 전부 시험.
KEY_PARAMS = ["accessKey", "ServiceKey", "serviceKey"]

# 승인 목록을 모르므로 '있을 법한 것'을 훑는다. 이름이 틀리면 ②로, 승인이 없으면
# ③으로 갈리므로, 틀린 후보가 섞여 있어도 판정 자체는 오염되지 않는다.
#
# 우리 화면에 필요한 것부터 순서를 잡았다:
#   서지 검색 → 특허 목록·분야 분류(지금 OPS 가 하는 일)
#   등록원부   → 권리변동(양도·이전) — EPO 에 없던 축, 거래 탭의 핵심 후보
#   해외특허   → 8대 분야 매트릭스를 지금처럼 전 세계 축으로 유지할 수 있는지
PROBES = [
    # (설명, 서비스/오퍼레이션, 질의 파라미터)
    ("특허·실용 자유검색",
     "patUtiliInfoSearchSevice/getWordSearch",
     {"word": "전력변환", "numOfRows": "3", "pageNo": "1"}),
    ("특허·실용 항목검색(분류·출원인·기간)",
     "patUtiliInfoSearchSevice/getAdvancedSearch",
     {"inventionTitle": "전력", "patent": "true", "utility": "false",
      "numOfRows": "3", "pageNo": "1"}),
    # 우리 분야 분류(CATEGORIES)는 CPC 접두를 쓴다. 이 서비스가 IPC 만 받는다면
    # Y04S·Y02E(계량·스마트그리드)는 IPC 에 없는 코드라 통째로 빈다 → 여기서 확인.
    ("분류 검색 — IPC 로 되나 (H02M)",
     "patUtiliInfoSearchSevice/getAdvancedSearch",
     {"ipcNumber": "H02M", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("분류 검색 — CPC 전용 코드가 되나 (Y04S)",
     "patUtiliInfoSearchSevice/getAdvancedSearch",
     {"ipcNumber": "Y04S", "patent": "true", "numOfRows": "3", "pageNo": "1"}),
    ("출원인 검색 — 국내 공급자 찾기의 출발점",
     "patUtiliInfoSearchSevice/applicantNameSearchInfo",
     {"applicantName": "엘에스일렉트릭", "numOfRows": "3", "pageNo": "1"}),
    ("서지 상세(요약·청구항 포함 여부)",
     "patUtiliInfoSearchSevice/getBibliographyDetailInfoSearch",
     {"applicationNumber": "1020200000001"}),
    # 아래부터는 이름 확신이 낮다. 404 로 떨어지면 그건 '없는 이름'이라는 정보다.
    ("등록원부(권리변동·양도 이력)",
     "rgstRightInfoService/getRgstRightList",
     {"applicationNumber": "1020200000001"}),
    ("심사 진행 상태",
     "patUtiliInfoSearchSevice/getExamStatusInfoSearch",
     {"applicationNumber": "1020200000001"}),
    ("해외특허 — 미국",
     "UsPatentInfoSearchService/getWordSearch",
     {"word": "power converter", "numOfRows": "3", "pageNo": "1"}),
    ("해외특허 — 유럽",
     "EpPatentInfoSearchService/getWordSearch",
     {"word": "power converter", "numOfRows": "3", "pageNo": "1"}),
]


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _fetch(url: str) -> tuple[int, str, str]:
    """(HTTP 코드, 본문, 오류설명). 코드 0 은 연결 자체가 안 된 것."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "ip-power-probe/1.0",
        "Accept": "application/xml, application/json;q=0.9, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
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


def _verdict(code: int, body: str, err: str) -> tuple[str, str]:
    """(판정, 근거). ①연결 ②이름 ③승인 을 갈라 준다."""
    if code == 0:
        return "연결안됨", err
    # 개발 환경의 세션 프록시는 허용목록 밖 호스트에 403 을 돌려준다. 그대로 두면
    # '승인 안 된 서비스'로 읽혀 정반대의 결론이 난다 — KIPRIS 가 보낸 403 과 구분한다.
    if "not in allowlist" in body or "egress" in body.lower():
        return "연결안됨", f"프록시가 막음(러너에서 돌려야 함) · {_squash(body, 100)}"
    rc, msg = _tag(body, "resultCode"), _tag(body, "resultMsg")
    up = body.upper()
    if code == 404 or "SERVICE ERROR" in up or "NO_OPENAPI_SERVICE" in up:
        return "이름틀림", f"HTTP {code} · {msg or _squash(body, 120)}"
    if code in (401, 403) or "SERVICE_KEY" in up or "SERVICE ACCESS DENIED" in up \
       or "미신청" in body or "권한" in body or (rc and rc not in ("00", "0")):
        return "미승인/키오류", f"HTTP {code} · resultCode={rc or '-'} {msg or _squash(body, 120)}"
    if code != 200:
        return "기타오류", f"HTTP {code} · {_squash(body, 120)}"
    return "열림", f"resultCode={rc or '-'} {msg or ''}".strip()


def _count(body: str) -> str:
    for t in ("totalCount", "count", "numOfRows"):
        v = _tag(body, t)
        if v:
            return f"{t}={v}"
    n = len(re.findall(r"<item[\s>]", body))
    return f"item={n}건" if n else "item 없음"


def _paths() -> list[tuple[str, str, dict]]:
    raw = (os.getenv("KIPRIS_PATHS") or "").split()
    if not raw:
        return PROBES
    out = []
    for spec in raw:
        path, _, qs = spec.partition("?")
        out.append(("(직접 지정)", path, dict(urllib.parse.parse_qsl(qs))))
    return out


def _live_base() -> str | None:
    """어느 스킴이 사는지 한 번만 확인하고 나머지에 재사용한다."""
    for base in BASES:
        code, _, err = _fetch(base + "/")
        print(f"  {base}  → " + (f"HTTP {code}" if code else f"실패 {err}"))
        if code:
            return base
    return None


def main() -> int:
    print("KIPRISplus 프로브 — 무엇이 열리는지 실측\n" + "=" * 66)
    print(f"키 길이: {len(KEY)}자" + ("  ← 0자면 시크릿이 전달되지 않은 것" if not KEY else ""))
    if not KEY:
        print("KIPRIS_KEY 가 비어 있습니다. Secrets 탭에 KIPRIS_KEY 로 등록했는지 확인하세요.")
        return 1

    print("\n① 연결 확인 — 러너에서 KIPRIS 서버에 닿는가")
    base = _live_base()
    if not base:
        print("  → 러너에서 닿지 않습니다. data.go.kr 과 같은 상황이면 "
              "로컬 실행 후 결과를 커밋하는 방식으로 가야 합니다.")
        return 2
    print(f"  → 사용 기준: {base}")

    fixed = (os.getenv("KIPRIS_KEYPARAM") or "").strip()
    key_params = [fixed] if fixed else KEY_PARAMS
    good_param = ""
    rows: list[tuple[str, str, str, str]] = []

    print("\n②③ 서비스별 — 이름이 맞는가 / 이 키로 승인돼 있는가")
    for label, path, params in _paths():
        # 키 질의 이름이 정해지면 그 뒤로는 그것만 쓴다(요청 수를 아낀다).
        tries = [good_param] if good_param else key_params
        best = None
        for kp in tries:
            q = dict(params)
            q[kp] = KEY
            url = f"{base}/{path}?" + urllib.parse.urlencode(q)
            code, body, err = _fetch(url)
            verdict, why = _verdict(code, body, err)
            if best is None or verdict == "열림":
                best = (verdict, why, body, kp)
            if verdict == "열림":
                good_param = good_param or kp
                break
            if verdict == "이름틀림":
                break            # 이름이 틀린 건 키 이름을 바꿔도 그대로다
        verdict, why, body, kp = best
        extra = _count(body) if verdict == "열림" else ""
        mark = {"열림": "✅", "미승인/키오류": "🔒", "이름틀림": "❓",
                "연결안됨": "⛔", "기타오류": "⚠️"}.get(verdict, "·")
        print(f"\n {mark} [{verdict}] {label}")
        print(f"    {path}   (키질의={kp})")
        print(f"    {why}" + (f" · {extra}" if extra else ""))
        if verdict == "열림":
            print("    ── 응답 앞부분 " + "-" * 40)
            print("    " + _squash(body, HEAD))
        rows.append((mark, verdict, label, path))

    print("\n" + "=" * 66)
    print("요약")
    for mark, verdict, label, path in rows:
        print(f"  {mark} {verdict:12s} {label}  [{path}]")
    ok = [r for r in rows if r[1] == "열림"]
    print(f"\n열린 서비스 {len(ok)}개 / 시험 {len(rows)}개"
          + (f" · 키 질의 이름 = {good_param}" if good_param else ""))
    print("판정 읽는 법:  ✅ 쓸 수 있다 · 🔒 이름은 맞고 승인이 없다(추가 신청) · "
          "❓ 이름이 틀렸다(후보 교체) · ⛔ 연결 자체가 안 된다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
