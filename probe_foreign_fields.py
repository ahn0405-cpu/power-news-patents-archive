"""해외 공보 응답이 **실제로 주는 필드 전부**를 찍는다 — 출원인 국적이 있는가.

왜 필요한가. 국적 미상 4,848건(CN 4,133 · JP 713)을 메울 길을 찾는 중인데,
확인한 것과 아직 확인 안 한 것이 섞여 있다.

  확인함  : 해외 서지상세(bibliographicInfo)에 출원인 국적 태그가 있는데 중국
            공보에서는 값이 빈칸이었다(응답 전문을 받아 봤다).
  확인 안 함: 해외 **검색**(advancedSearch) 응답의 전체 필드 목록. 우리는 열 개
            남짓만 읽는데 그게 응답의 전부인지 본 적이 없다. 안 읽는 필드에
            국적이 들어 있을 수도 있다.

'collectionValues(국가코드)로 국적을 알 수 있지 않나' 라는 물음도 여기서 갈린다.
그것은 **입력** 파라미터이고 응답에서 대응하는 것은 countryCode — 우리가 이미
office(공개 특허청)로 저장하는 값이다. 출원인 국적과 다른 축이라고 보고 있지만,
그 판단도 응답을 통째로 보고 말하는 편이 낫다.

번호를 손으로 적지 않는다. **검색으로 받은 그 문헌의 ltrtno 를 그대로** 서지상세에
넣는다 — 표기가 안 맞아서 빈 결과가 오는 경우와 진짜로 값이 없는 경우를 섞지
않으려면 이렇게 해야 한다(공개번호로는 빈 결과가 온다는 것이 이미 실측돼 있다).

대조군을 함께 본다. 중국·일본에서 값이 안 오더라도 미국에서 오면 '그 공보에
없는 것' 이고, 미국에서도 안 오면 '우리 호출이 틀린 것' 이다 — 다음 수가 정반대다.

사용: KIPRIS_KEY=... python probe_foreign_fields.py
키는 절대 파일에 적지 않는다. GitHub Secret → 환경변수로만 받는다.
"""
from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import patent_config as cfg

TIMEOUT = 30
OFFICES = ["CN", "JP", "US"]        # US 가 대조군이다
IPC = "H02J3"                       # 계통 — 어느 나라에나 많다


def _fetch(url: str) -> ET.Element | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": "ip-power/1.0", "Accept": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except Exception as e:                                  # noqa: BLE001
        print(f"    x {type(e).__name__}: {e}")
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        txt = re.sub(r"\s+", " ", raw.decode("utf-8", "replace"))[:240]
        print(f"    x XML 이 아니다: {txt}")
        return None


def _search_url(params: dict) -> str:
    q = dict(params)
    q[cfg.FOREIGN_KEYPARAM] = cfg.KIPRIS_KEY
    return (f"{cfg.FOREIGN_BASE}/{cfg.FOREIGN_SERVICE}/{cfg.FOREIGN_OP}?"
            + urllib.parse.urlencode(q))


def _bib_url(lit: str, office: str) -> str:
    q = {"literatureNumber": lit, "countryCode": office,
         cfg.ORIGIN_KEYPARAM: cfg.KIPRIS_KEY}
    return (f"{cfg.ORIGIN_BASE}/{cfg.ORIGIN_SERVICE}/{cfg.ORIGIN_OP}?"
            + urllib.parse.urlencode(q))


_NAT = ("nation", "country", "residen", "addr", "state")


def _dump(node: ET.Element, indent: str = "      ") -> tuple[int, list]:
    """자식 태그를 하나도 빠짐없이 찍는다. 값이 비어 있어도 **태그가 있다는 사실**이
    중요하다 — '필드가 없다' 와 '필드는 있는데 비었다' 는 다음 수가 다르다."""
    n, hits = 0, []
    for ch in node:
        val = (ch.text or "").strip()
        low = ch.tag.lower()
        mark = ""
        if any(k in low for k in _NAT):
            mark = "   <- 국적/주소 계열" + ("" if val else "  (값이 비어 있다)")
            hits.append((ch.tag, val))
        print(f"{indent}{ch.tag} = {val[:70]!r}{mark}")
        n += 1
        sub_n, sub_h = _dump(ch, indent + "  ")
        n += sub_n
        hits += sub_h
    return n, hits


SAMPLE_N = 40          # 나라마다 이만큼 훑어 '채워진 비율' 을 잰다


def _fill_rates() -> None:
    """표본을 넉넉히 훑어 **INID 항목이 실제로 채워지는 비율**을 잰다.

    앞의 한 건짜리 덤프는 '필드가 있나' 를 본 것이고, 여기서는 '값이 오나' 를
    센다. 둘은 다르다 — 두 건 보고 '비어 있다' 고 단정하면 안 된다.

    무엇을 세나(특허 서지의 표준 항목):
      · INID (71) 출원인 주소/국가  → applicantCountry (서지상세)
      · INID (30) 우선권            → priorityNo · priorityDate (검색 응답)
      · 패밀리                       → familyNo
    (30) 이나 패밀리가 채워지면, 중국 공보를 그 특허의 미국·유럽 가족 문헌과
    이어서 국적을 물려받을 수 있다 — KIPRIS 안에 남은 마지막 길이다.
    """
    print(f"\n{'='*62}\n채워진 비율 (나라마다 {SAMPLE_N}건)\n{'='*62}")
    print("  한 건 덤프는 '필드가 있나' 였고, 여기서는 '값이 오나' 를 센다.\n")
    for office in OFFICES:
        root = _fetch(_search_url({
            "ipc": IPC, "collectionValues": office,
            "currentPage": "1", "docsCount": str(SAMPLE_N)}))
        if root is None:
            print(f"  [{office}] 검색 실패")
            continue
        rows = root.findall(".//searchResult")
        if not rows:
            print(f"  [{office}] 결과 없음")
            continue
        n = len(rows)
        cnt = {"priorityNo": 0, "priorityDate": 0, "familyNo": 0,
               "internationalApplicationNo": 0}
        lits = []
        for r in rows:
            for k in cnt:
                if (r.findtext(k) or "").strip():
                    cnt[k] += 1
            lit = (r.findtext("ltrtno") or "").strip()
            if lit:
                lits.append(lit)
        print(f"  [{office}] 검색 {n}건")
        for k, v in cnt.items():
            print(f"      {k:28s} {v:3d}/{n}  ({v*100//n}%)")
        # 서지상세는 건마다 한 번씩 부른다 → 표본을 줄인다(초당 한도는 넉넉하다).
        got = blank = 0
        for lit in lits[:15]:
            r2 = _fetch(_bib_url(lit, office))
            if r2 is None:
                continue
            v = (r2.findtext(".//applicantCountry") or "").strip()
            if v:
                got += 1
            else:
                blank += 1
        tot = got + blank
        if tot:
            print(f"      applicantCountry (INID 71)   {got:3d}/{tot}  ({got*100//tot}%)")
        print()


def main() -> int:
    if not cfg.KIPRIS_KEY:
        raise SystemExit("KIPRIS_KEY 가 없습니다 (GitHub Secret 확인)")

    verdict = {}
    for office in OFFICES:
        tag = "대조군" if office == "US" else "미상이 쌓인 곳"
        print(f"\n{'='*62}\n[{office}] {tag}\n{'='*62}")

        print("· 1) 검색(advancedSearch) 응답의 전체 필드")
        root = _fetch(_search_url({
            "ipc": IPC, "collectionValues": office,
            "currentPage": "1", "docsCount": "1"}))
        lit = ""
        if root is None:
            verdict[office] = "검색 실패"
            continue
        rows = root.findall(".//searchResult")
        if not rows:
            print("    ! searchResult 가 없다 — 응답 뿌리부터 찍는다")
            _dump(root)
            verdict[office] = "검색 결과 없음"
            continue
        cnt, hits = _dump(rows[0])
        lit = (rows[0].findtext("ltrtno") or "").strip()
        print(f"    -> 필드 {cnt}개 · 국적 계열 태그 {len(hits)}개 · ltrtno={lit!r}")

        print("\n· 2) 그 문헌의 서지상세(bibliographicInfo)")
        if not lit:
            print("    ! ltrtno 가 없어 서지상세를 부를 열쇠가 없다")
            verdict[office] = f"검색 필드 {cnt}개 · 국적 {len(hits)}개 · ltrtno 없음"
            continue
        r2 = _fetch(_bib_url(lit, office))
        if r2 is None:
            verdict[office] = f"검색 필드 {cnt}개 · 서지상세 실패"
            continue
        items = r2.findall(".//applicantInfo") or r2.findall(".//item") or [r2]
        cnt2, hits2 = _dump(items[0])
        filled = [(t, v) for t, v in hits2 if v]
        print(f"    -> 필드 {cnt2}개 · 국적 계열 {len(hits2)}개 · 그중 값이 온 것 {len(filled)}개")
        verdict[office] = (f"검색 국적태그 {len(hits)}개 / "
                           f"서지상세 국적태그 {len(hits2)}개 · 값 있음 {len(filled)}개"
                           + (f" {filled[:2]}" if filled else ""))

    print(f"\n{'='*62}\n판정\n{'='*62}")
    for off in OFFICES:
        print(f"  {off}: {verdict.get(off, '(못 봄)')}")
    _fill_rates()

    print("\n  읽는 법")
    print("   · 대조군 US 에서 값이 오고 CN·JP 에서 안 오면 → 그 공보에 원래 없다.")
    print("     KIPRIS 경로로는 끝이고, 다른 출처를 붙이는 수밖에 없다.")
    print("   · US 에서도 안 오면 → 우리 호출이나 열쇠가 틀렸다. 고치면 된다.")
    print("   · CN·JP 에서 값이 오면 → 수집기에 붙인다. 미상 4,848건이 줄어든다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
