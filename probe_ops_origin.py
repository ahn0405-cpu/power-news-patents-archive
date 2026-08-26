"""EPO OPS 로 **중국·일본 공보의 출원인 국적**을 얻을 수 있는지 확인한다.

왜 필요한가. 우리 아카이브에서 출원인 국적이 비어 있는 4,848건은 거의 전부
중국·일본 공보다(CN 4,133 · JP 713 · US 2). KIPRIS 해외 서지상세에는 그 값이
**원본부터 없다** — 응답 전문을 받아 확인했다(태그는 있고 값이 빈칸).

KIPRIS 안에서 메울 길은 다 막혀 있다(실측):
  · 미상 출원인 2,931곳 중 US·EP 공보를 가진 곳은 2곳뿐 → 두드릴 대상이 없다
  · 이름으로 잇기 0건 → 중·일 출원인은 아카이브 다른 어디에도 없다
  · '공개국 = 국적' 추정은 검증 표본이 없다(CN-only 5곳 · JP-only 0곳).
    US-only 가 실제 미국 37%, EP-only 가 유럽 48%인 걸 보면 위험한 가정인데,
    중·일에 대해서는 맞는지 틀리는지 **잴 수가 없다**.

남은 수가 OPS 다. 키가 이미 있고(OPS_KEY/OPS_SECRET), 이 프로젝트가 예전에
OPS 로 수집했으니 경로도 검증돼 있다. OPS 서지의 applicant 노드에는 residence
(거주국) 나 address 가 실려 오는 판본이 있는데, **중국·일본 공보에도 그 값이
오는지는 확인된 적이 없다.** 추측으로 수집기를 고치고 일주일을 기다리는 대신
여기서 한 번 확인한다.

무엇을 보나:
  1) 토큰이 나오나 (자격 확인)
  2) 공개번호 하나로 서지를 받을 수 있나 (docdb / epodoc 두 표기 모두 시도)
  3) applicant 노드에 나라가 실려 오나 — **노드를 통째로 찍는다**. 필드 이름을
     미리 안다고 가정하지 않는다(residence/country, address/country, @country …)
  4) 대조군: 국적을 이미 아는 US·EP 공보에서는 어떻게 오나

사용: OPS_KEY=... OPS_SECRET=... python probe_ops_origin.py
키는 절대 파일에 적지 않는다. GitHub Secret → 환경변수로만 받는다.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"
BIB_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/{fmt}/{num}/biblio"
TIMEOUT = 30

# 우리 아카이브에서 **실제로 국적이 비어 있는** 문헌들이다. 만들어 낸 번호가
# 아니라 지금 화면에서 '미상' 으로 세어지고 있는 바로 그 건들 → 여기서 국적이
# 나오면 그 숫자가 그만큼 줄어든다.
SAMPLES = [
    ("CN", "CN122178381A", "BEIJING SMARTCHIP MICROELECTRONICS (미상)"),
    ("CN", "CN122178360A", "SICHUAN UNIVERSITY (미상)"),
    ("CN", "CN122178454A", "HUNAN ELECTRIC POWER DESIGN (미상)"),
    ("JP", "JP2026126572A", "株式会社カネカ (미상)"),
    ("JP", "JP2026126601A", "大阪瓦斯株式会社 (미상)"),
    ("JP", "JP2026126883A", "日産自動車株式会社 (미상)"),
    # 대조군 — 이 둘은 KIPRIS 에서 이미 국적을 얻었다. OPS 가 같은 답을 주는지
    # 보면, 값이 안 오는 것이 '공보에 없어서' 인지 '우리 호출이 틀려서' 인지 갈린다.
    ("US", "US20260219698A1", "대조군 · KIPRIS 는 US 라고 한다"),
    ("EP", "EP4787653A1", "대조군 · KIPRIS 는 JP 라고 한다"),
]


def _body(e) -> str:
    try:
        raw = e.read().decode("utf-8", "replace")
    except Exception:
        return "(본문 없음)"
    txt = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", txt).strip()[:300] or "(본문 비어 있음)"


def _token() -> str:
    key, sec = os.getenv("OPS_KEY", ""), os.getenv("OPS_SECRET", "")
    if not key or not sec:
        raise SystemExit("OPS_KEY/OPS_SECRET 이 없습니다 (GitHub Secret 확인)")
    cred = base64.b64encode(f"{key}:{sec}".encode()).decode()
    req = urllib.request.Request(
        AUTH_URL, data=b"grant_type=client_credentials",
        headers={"Authorization": "Basic " + cred,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())["access_token"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"토큰 실패 {e.code}: {_body(e)}")


def _biblio(token: str, fmt: str, num: str):
    """(응답 dict, 사유). 실패해도 예외를 올리지 않는다 — 표기를 여러 개 시도한다."""
    url = BIB_URL.format(fmt=fmt, num=urllib.parse.quote(num, safe=""))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read()), ""
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {_body(e)[:120]}"
    except Exception as e:                                  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _walk(node, path=""):
    """dict/list 를 훑으며 (경로, 값) 을 낸다. 필드 이름을 미리 안다고 가정하지
    않으려고 통째로 훑는다 — OPS 는 판본마다 노드 이름이 다르다."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def _applicants(data: dict) -> list:
    """응답 어디에 있든 applicant 노드를 찾아 낸다."""
    out = []

    def rec(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "applicant":
                    out.extend(v if isinstance(v, list) else [v])
                else:
                    rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)

    rec(data)
    return out


def main() -> int:
    print("· OPS 토큰")
    token = _token()
    print("  ok  토큰 발급됨\n")

    found = {"CN": 0, "JP": 0, "US": 0, "EP": 0}
    tried = {"CN": 0, "JP": 0, "US": 0, "EP": 0}
    for office, num, note in SAMPLES:
        tried[office] += 1
        print(f"· [{office}] {num} — {note}")
        data, why, used = None, "", ""
        # docdb 표기가 정식이지만 우리 번호는 epodoc 모양이다 → 둘 다 시도한다.
        for fmt in ("epodoc", "docdb"):
            data, why = _biblio(token, fmt, num)
            if data:
                used = fmt
                break
            print(f"    {fmt}: {why}")
        if not data:
            print("    ✗ 서지를 못 받았다\n")
            continue
        apps = _applicants(data)
        print(f"    ok  {used} 표기로 서지 받음 · applicant 노드 {len(apps)}개")
        if not apps:
            print("    ! applicant 노드가 없다 — 응답 구조를 본다")
            for p, v in list(_walk(data))[:25]:
                print(f"      {p} = {str(v)[:60]}")
            print()
            continue
        # 나라처럼 보이는 값을 노드 안에서 찾는다(이름을 미리 정하지 않는다).
        hit = False
        for i, a in enumerate(apps[:3]):
            print(f"    applicant[{i}] 전체:")
            for p, v in _walk(a):
                s = str(v)
                mark = ""
                if re.fullmatch(r"[A-Z]{2}", s.strip()):
                    mark = "   ← 나라 코드로 보임"
                    hit = True
                print(f"      {p} = {s[:70]}{mark}")
        if hit:
            found[office] += 1
        print()

    print("── 판정 ─────────────────────────────────────")
    for off in ("CN", "JP", "US", "EP"):
        if not tried[off]:
            continue
        print(f"  {off}: {tried[off]}건 중 {found[off]}건에서 나라 코드가 보였다")
    cn_jp = found["CN"] + found["JP"]
    if cn_jp:
        print(f"\n  → 중국·일본 공보에서 국적을 얻을 수 있다({cn_jp}건 확인).")
        print("     위 경로를 그대로 수집기에 붙이면 미상 4,848건이 줄어든다.")
    elif found["US"] or found["EP"]:
        print("\n  → 대조군(US·EP)에서는 나오는데 중국·일본에서는 안 나온다.")
        print("     호출이 틀린 게 아니라 그 공보에 값이 없는 것이다 — KIPRIS 와 같은 한계다.")
    else:
        print("\n  → 어디서도 안 나왔다. 호출이나 표기가 틀렸을 수 있다(위 응답 구조를 볼 것).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
