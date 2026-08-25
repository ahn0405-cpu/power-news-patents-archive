"""해외 출원인의 **국적**을 서지상세로 채운다.

왜 따로 있나
--------------------------------------------------------------------
해외 검색(ForeignPatentAdvencedSearchService)의 응답에는 출원인 국적 칸이 없다.
그래서 큐레이션 별칭표에 이름이 걸리는 곳(Siemens·Toyota…)만 국적이 붙고 나머지는
비어 있었다 — 실측(2026-08-25) 출원인 5,471곳 가운데 4,256곳이 미상이고, 미상이
등장한 건수는 중국 4,542 · 일본 2,039 · 미국 1,042 · 유럽 838 이었다.

공개국(countryCode)으로 대신 채우는 것은 하지 않는다. 실제로 갈린다:
  US 공개 202600213551A1 → Panasonic … Co., Ltd. / applicantCountry **JP**
  US 공개 202600221299A1 → Tsinghua University    / applicantCountry **CN**
공개국으로 채웠으면 둘 다 미국 기업이 됐을 것이다.

**서지상세**(ForeignPatentBibliographicService/bibliographicInfo)에는
applicantCountry 가 있다(위 두 건이 그 실측이다). 다만 건당 1요청이고 응답이
무겁다(초록·청구항까지 실려 온다). 그래서 세 가지로 비용을 눌렀다.

  1) **출원인당 1회**만 부른다. 국적은 출원인의 성질이지 문서의 성질이 아니므로,
     그 출원인의 문서 하나만 보면 된다. 16,265건이 아니라 4,256곳이 대상이다.
  2) 한 실행에 상한(ORIGIN_PER_RUN)을 두고 stats.json 에 **누적**한다. 매일
     조금씩 채워 며칠에 걸쳐 메운다 — OPS 시절 공개국 집계와 같은 방식이다.
  3) 몇 갈래로 나눠 동시에 부른다. 하나가 느리다고 줄 전체가 서지 않게.

주의
--------------------------------------------------------------------
· 조회 열쇠는 **ltrtno**(문헌번호, 예: 202600213551A1)다. 공개번호로는 빈 결과가
  온다(실측). 항목에 ltrtno 를 저장해 두고 있다(수집기 참조). OPS 시절 항목에는
  없으므로 그런 것은 건너뛴다.
· 성공/실패 판정이 국내와 반대다 — 해외는 정상일 때 resultCode 가 **비어 있다**.
· 번호 표기가 맞지 않는 문헌이 섞여 있으면 매 실행 같은 것을 다시 두드리며 상한을
  써 버린다 → 실패 횟수를 세어 ORIGIN_MAX_TRY 를 넘으면 그만둔다.

키는 GitHub Secret(KIPRIS_KEY) → 환경변수로만 받는다. 파일에 적지 않는다.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import patent_config as cfg


def _url(lit: str, office: str) -> str:
    q = {"literatureNumber": lit, "countryCode": office,
         cfg.ORIGIN_KEYPARAM: cfg.KIPRIS_KEY}
    return (f"{cfg.ORIGIN_BASE}/{cfg.ORIGIN_SERVICE}/{cfg.ORIGIN_OP}?"
            + urllib.parse.urlencode(q))


def _fetch(lit: str, office: str) -> str | None:
    """(국적 코드) 또는 None. 예외는 삼킨다 — 한 건 실패로 실행이 죽으면 안 된다."""
    # 여기서는 어떤 예외도 밖으로 내보내지 않는다. 이 보강은 '있으면 좋은' 것이라
    # 한 건이 이상하다고 매일 도는 빌드를 죽이면 안 된다. http.client 쪽 예외는
    # OSError 계열이 아니라 따로 잡히지도 않는다 → 통째로 삼키고 다음에 다시 한다.
    try:
        req = urllib.request.Request(_url(lit, office),
                                     headers={"Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=cfg.ORIGIN_TIMEOUT) as r:
            body = r.read()
        root = ET.fromstring(body)
    except Exception:
        return None
    # 해외는 정상일 때 resultCode 가 비어 있다(국내와 반대) — 채워져 있으면 실패다.
    if (root.findtext(".//resultCode") or "").strip():
        return None
    cc = (root.findtext(".//applicantInfo/applicantCountry") or "").strip()
    return cc.upper() or None


def targets(weeks: dict[str, dict], store: dict) -> list[tuple[str, str, str, int]]:
    """채울 대상 (출원인, 문헌번호, 공개국, 건수). 건수 많은 곳부터.

    건수 순으로 하는 이유: 화면에서 먼저 눈에 띄는 곳부터 맞아 간다. 며칠에 걸쳐
    채우는 동안에도 상위 랭킹과 매트릭스는 빨리 정확해진다.
    """
    have = store.get("origins") or {}
    tried = store.get("originTry") or {}
    seen: dict[str, list] = {}          # 이름 → [문헌번호, 공개국, 건수]
    for wk in sorted(weeks):
        for p in weeks[wk].get("patents", []):
            name = (p.get("applicant") or "").strip()
            if not name or p.get("country"):        # 이미 국적을 아는 항목
                continue
            if name in have or tried.get(name, 0) >= cfg.ORIGIN_MAX_TRY:
                continue
            lit, office = p.get("ltrtno"), p.get("office")
            if not lit or not office:               # OPS 시절 항목 — 열쇠가 없다
                continue
            row = seen.get(name)
            if row is None:
                seen[name] = [lit, office, 1]
            else:
                row[2] += 1
    out = [(n, v[0], v[1], v[2]) for n, v in seen.items()]
    out.sort(key=lambda t: (-t[3], t[0]))
    return out


def collect(weeks: dict[str, dict], store: dict) -> tuple[dict, dict]:
    """국적을 채운다. 반환 ({이름: 국적코드}, {이름: 실패횟수}).

    반환값은 stats 에 **병합**된다(덮어쓰지 않는다) — 한 실행이 일부만 채우는
    설계라서, 통째로 대입하면 지난 실행이 채운 것이 지워진다.
    """
    if not cfg.ORIGIN or cfg.ORIGIN_PER_RUN <= 0:
        print("  출원인 국적 보강: 꺼져 있음")
        return {}, {}
    if not cfg.KIPRIS_KEY:
        print("  출원인 국적 보강: 키가 없어 건너뜁니다")
        return {}, {}

    todo = targets(weeks, store)
    rest = max(0, len(todo) - cfg.ORIGIN_PER_RUN)
    todo = todo[:cfg.ORIGIN_PER_RUN]
    if not todo:
        print("  출원인 국적 보강: 채울 곳이 없습니다")
        return {}, {}

    def one(t):
        name, lit, office, _ = t
        return name, _fetch(lit, office)

    with ThreadPoolExecutor(max_workers=max(1, cfg.ORIGIN_WORKERS)) as pool:
        results = list(pool.map(one, todo))

    got: dict[str, str] = {}
    fail: dict[str, int] = {}
    prev = store.get("originTry") or {}
    for name, cc in results:
        if cc:
            got[name] = cc
        else:
            fail[name] = prev.get(name, 0) + 1
    by_cc: dict[str, int] = {}
    for cc in got.values():
        by_cc[cc] = by_cc.get(cc, 0) + 1
    top = " ".join(f"{k} {v}" for k, v in
                   sorted(by_cc.items(), key=lambda x: -x[1])[:6])
    print(f"  출원인 국적 보강: {len(todo)}곳 조회 → {len(got)}곳 확인"
          f" · 실패 {len(fail)}곳 · 남은 곳 {rest:,}")
    if top:
        print(f"    확인된 국적: {top}")
    return got, fail
