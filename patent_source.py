"""전력 특허 수집 (Google Patents 비공식 JSON, 무API·무키) — 출원인 × 분야.

큐레이션한 주요 출원인마다, 8개 전력 분야의 제목 키워드로 교집합 조회한다:
  assignee="<출원인>" & q=TI="<분야 용어>" & country=<국적> & sort=new
→ '그 출원인이 그 분야에 낸 최신 특허'를 정밀하게 얻는다(질의어가 곧 분야 태그).

프로브(Actions)로 실측 확인한 문법이며, KR 특허도 Google 은 영문 제목으로 색인하므로
분야 키워드는 영어 하나로 KR/US 공통 사용한다. 검색 결과에 CPC 는 없다.

비공식 엔드포인트라 실패에 관대하다(개별 실패는 건너뛰고, 전부 실패/오프라인이거나
PATENT_MOCK=on 이면 재현 가능한 MOCK 으로 폴백). 특허가 비어도 뉴스 탭은 정상 빌드된다.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import patent_config as cfg

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_BASE = "https://patents.google.com/xhr/query"


def _build_url(assignee_q: str, term: str) -> str:
    # 내부 검색질의를 통째로 url= 에 '한 번만' 인코딩. assignee 전용 파라미터(정밀)와
    # 제목 정확검색 TI= 을 AND 로 결합한다. country 미지정 → 전 세계 공개에서 조회(재현율↑,
    # 중/일/유럽 특허도 Google 영문 제목으로 색인). (프로브로 문법·재현율 확인함)
    inner = f'assignee={assignee_q}&q=TI="{term}"&sort=new'
    return f"{_BASE}?url={urllib.parse.quote(inner, safe='')}&exp="


def _parse_json(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace").lstrip()
    if text.startswith(")]}'"):      # XSSI 보호 프리픽스 제거
        text = text.split("\n", 1)[-1]
    return json.loads(text)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")   # <b> 하이라이트 등 제거
    return html.unescape(text).strip()


def _fmt_date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd:
        return None
    s = str(yyyymmdd).replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _office(num: str) -> str:
    """공개번호 접두 2글자로 실제 발행 특허청 추정(US/KR/CN/JP/EP/WO...)."""
    m = re.match(r"[A-Za-z]{2}", num or "")
    return m.group(0).upper() if m else ""


def _normalize(pat: dict, pid: str) -> dict:
    num = pat.get("publication_number") or ""
    link = (f"https://patents.google.com/{pid}" if pid
            else f"https://patents.google.com/patent/{num}/en" if num
            else "https://patents.google.com/")
    return {
        "number": num,
        "title": _clean(pat.get("title", "")),
        "assignee": _clean(pat.get("assignee", "")),
        "inventor": _clean(pat.get("inventor", "")),
        "pub_date": _fmt_date(pat.get("publication_date")),
        "filing_date": _fmt_date(pat.get("filing_date")),
        "snippet": _clean(pat.get("snippet", ""))[:220],
        "office": _office(num),     # 실제 발행 특허청(참고용)
        "url": link,
    }


def _fetch(assignee_q: str, term: str) -> list[dict]:
    url = _build_url(assignee_q, term)
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "Referer": "https://patents.google.com/",
    })
    with urllib.request.urlopen(req, timeout=cfg.REQUEST_TIMEOUT) as r:
        data = _parse_json(r.read())
    out = []
    clusters = (data.get("results", {}) or {}).get("cluster", []) or []
    for cl in clusters:
        for res in cl.get("result", []) or []:
            pat = res.get("patent", {}) or {}
            pid = res.get("id", "")
            out.append(_normalize(pat, pid))
    return out


def _dedup_key(p: dict) -> str:
    return (p.get("number") or "").upper() or re.sub(r"[\s\W_]+", "", p.get("title", "").lower())


# 무선전력전송(Qi 충전 등)은 우리 주제(전력계통)와 무관한데 'power transmission'에 걸린다 → 제외.
_EXCLUDE_TITLE = re.compile(r"wireless|무선", re.I)


def _is_offtopic(p: dict) -> bool:
    return bool(_EXCLUDE_TITLE.search(p.get("title", "")))


def _live_collect(rotate: int = 0) -> list[dict]:
    collected: list[dict] = []
    seen: set[str] = set()
    errors = 0
    total_q = 0
    # 무키 엔드포인트는 실행당 ~수십~100요청 뒤 차단한다 → 매 실행 시작점을 회전시켜
    # 매주 다른 출원인 부분집합을 수집하고, 아카이브(주별 누적)로 매트릭스를 채운다.
    n = len(cfg.APPLICANTS)
    order = cfg.APPLICANTS[rotate % n:] + cfg.APPLICANTS[:rotate % n] if n else []
    for ap in order:
        ap_added = 0
        for cat in cfg.CATEGORIES:
            pair_added = 0            # (출원인×분야) 조합 상한
            for term in cat["terms"]:
                total_q += 1
                try:
                    items = _fetch(ap["q"], term)
                except Exception as e:
                    errors += 1
                    print(f"  ! [{ap['name']}/{cat['key']}] '{term}' 실패: {e}")
                    if cfg.REQUEST_DELAY:
                        time.sleep(cfg.REQUEST_DELAY)
                    continue
                for it in items:
                    key = _dedup_key(it)
                    if not key or key in seen:
                        continue
                    if _is_offtopic(it):             # 무선전력 등 무관 특허 배제
                        continue
                    seen.add(key)
                    it["category"] = cat["key"]      # 질의어 = 분야 태그
                    it["applicant"] = ap["name"]      # 큐레이션 대표명(우리가 조회한 주체)
                    it["country"] = ap["region"]      # 지역 그룹(미국·한국·중국·일본·유럽)
                    it["flag"] = ap["flag"]           # 행 국기(국적)
                    collected.append(it)
                    pair_added += 1
                    ap_added += 1
                    if pair_added >= cfg.PER_PAIR_LIMIT:
                        break
                if cfg.REQUEST_DELAY:
                    time.sleep(cfg.REQUEST_DELAY)
                if pair_added >= cfg.PER_PAIR_LIMIT:
                    break
        print(f"  · {ap['flag']} {ap['name']} ({ap['region']}): {ap_added}건")
    if not collected and errors >= max(1, total_q):
        raise RuntimeError("모든 특허 쿼리 실패(차단/오프라인 추정)")
    return collected


# ── MOCK (오프라인/차단 시 폴백) — 출원인 × 분야 표본 합성 ─────────────
# 각 출원인에게 '그 회사다운' 분야 몇 개를 배정해 매트릭스가 채워지게 한다.
_AP_FIELDS = {
    "General Electric": ["nuclear", "grid", "industry"], "GE Vernova": ["grid", "nuclear"],
    "한국전력공사": ["grid", "supply", "meter"], "한국전력기술": ["nuclear"],
    "HD현대일렉트릭": ["grid", "industry"], "효성중공업": ["grid", "industry"],
    "LS일렉트릭": ["grid", "industry", "meter"], "삼성전자": ["mega", "renew", "datacenter"],
    "State Grid": ["grid", "supply", "meter"], "Huawei": ["datacenter", "mega"],
    "CATL": ["renew"], "Mitsubishi Electric": ["mega", "grid"],
    "Siemens": ["grid", "industry", "supply"], "ABB": ["grid", "industry"],
    "Schneider Electric": ["datacenter", "industry", "supply"],
}
_PREFIX = {"US": "US2026", "KR": "KR102026", "CN": "CN2026", "JP": "JP2026", "EU": "EP2026"}


def _mock_collect(today: datetime) -> list[dict]:
    seed = int(hashlib.md5(today.strftime("%Y-%m-%d").encode()).hexdigest()[:8], 16)
    collected = []
    for ai, ap in enumerate(cfg.APPLICANTS):
        fields = _AP_FIELDS.get(ap["name"], ["grid"])
        for fi, fkey in enumerate(fields):
            cat = cfg.CATEGORY_BY_KEY.get(fkey)
            if not cat:
                continue
            term = cat["terms"][0]
            days_ago = (seed + ai * 3 + fi * 5) % max(1, cfg.LOOKBACK_DAYS)
            pub = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            serial = 10000 + (seed + ai * 37 + fi * 101) % 90000
            num = f"{_PREFIX.get(ap['region'], 'US2026')}{serial}A1"
            collected.append({
                "number": num,
                "title": f"[{ap['name']}] {term} related apparatus",
                "assignee": ap["name"],
                "inventor": "",
                "pub_date": pub,
                "filing_date": None,
                "snippet": "[샘플 데이터] 네트워크 차단/오프라인 환경의 미리보기용 항목입니다.",
                "office": _office(num),
                "country": ap["region"],
                "flag": ap["flag"],
                "category": fkey,
                "applicant": ap["name"],
            })
    return collected


def _rotation(today: datetime) -> int:
    """이번 실행의 출원인 시작 오프셋. PATENT_ROTATE 지정 시 그 값, 없으면 주차 기반.

    주차 기반이면 매주 목록의 절반씩 앞으로 당겨(스로틀로 뒤가 잘려도 다음 주에 커버).
    """
    import os
    env = os.getenv("PATENT_ROTATE")
    if env not in (None, ""):
        try:
            return int(env)
        except ValueError:
            pass
    week = today.isocalendar()[1]
    half = max(1, len(cfg.APPLICANTS) // 2)
    return (week * half) % max(1, len(cfg.APPLICANTS))


def collect(today: datetime) -> tuple[list[dict], bool]:
    """(출원인×분야) 최신 특허 목록과 mock 여부를 반환.

    PATENT_MOCK=on → mock / off → 라이브(실패 시 예외) / auto → 라이브 후 실패 시 mock.
    출원인 시작점은 회전(주차 기반 또는 PATENT_ROTATE)해 매주 다른 부분집합을 모은다.
    """
    if cfg.is_mock():
        return _mock_collect(today), True
    try:
        return _live_collect(_rotation(today)), False
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ 특허 라이브 수집 실패 → MOCK 폴백: {e}")
        return _mock_collect(today), True
