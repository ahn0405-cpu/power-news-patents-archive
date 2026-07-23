"""전력 특허 수집 (Google Patents 비공식 JSON, 무API·무키).

Google Patents 프런트엔드가 쓰는 xhr/query 엔드포인트를 사용한다. 카테고리·국가별로
'최근 N일 공개(publication)' 특허를 키워드로 조회 → 표준 dict 로 반환한다.

비공식 엔드포인트라 언제든 막힐 수 있으므로 실패에 관대하다:
  - 개별 쿼리 실패는 건너뛴다.
  - 전부 실패(차단/오프라인)하거나 PATENT_MOCK=on 이면 재현 가능한 MOCK 으로 폴백한다.
  → 특허 수집이 실패해도 사이트(특히 뉴스 탭)는 항상 정상 빌드된다.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import patent_config as cfg

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_BASE = "https://patents.google.com/xhr/query"


def _build_url(term: str, country: str, cpc: str = "") -> str:
    # 내부 검색 질의(q=키워드&country=..&sort=new)를 통째로 url= 파라미터에 '한 번만'
    # 인코딩해 넣는다. urlencode 로 미리 인코딩한 뒤 다시 quote 하면 특수문자가
    # 이중 인코딩돼 엔드포인트가 500 을 반환한다.
    #
    # 절대 날짜창(before/after=publication:...) 대신 sort=new(최신 공개순)를 쓴다.
    # 날짜창은 실행 환경 시계가 실제와 어긋나면(미래 날짜) 결과가 0이 되기 때문.
    # '최신순 상위 N + 아카이브 중복제거'로 매주 새로 공개된 특허만 누적한다.
    # 제목 필드 정확검색: q=TI="용어" → 제목에 그 용어가 있어야 매칭.
    # (Actions 프로브로 지원·정밀도 실측 확인. 무선통신·AI 특허는 제목이 달라 배제됨.)
    # cpc 는 옵션(제공 시 AND 로 추가 잠금). 콤마 멀티CPC 는 AND(교집합)라 쓰지 않는다.
    inner = f'q=TI="{term}"&country={country}&sort=new'
    if cpc:
        inner += f"&cpc={cpc}"
    return f"{_BASE}?url={urllib.parse.quote(inner, safe='')}&exp="


def _parse_json(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace").lstrip()
    if text.startswith(")]}'"):      # XSSI 보호 프리픽스 제거
        text = text.split("\n", 1)[-1]
    return json.loads(text)


def _fetch(term: str, country: str, cpc: str = "") -> list[dict]:
    url = _build_url(term, country, cpc)
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
            pid = res.get("id", "")   # 예: "patent/KR20260012345A/en"
            out.append(_normalize(pat, pid, country))
    return out


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def _fmt_date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd:
        return None
    s = str(yyyymmdd).replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _normalize(pat: dict, pid: str, country: str) -> dict:
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
        "country": country,
        "url": link,
    }


def _dedup_key(p: dict) -> str:
    return (p.get("number") or "").upper() or re.sub(r"[\s\W_]+", "", p.get("title", "").lower())


def _live_collect() -> list[dict]:
    collected: list[dict] = []
    seen: set[str] = set()
    errors = 0
    total_q = 0
    for cat in cfg.CATEGORIES:
        cat_added = 0
        for country in cfg.COUNTRIES:
            # 국가별로 상한을 따로 둔다 → 먼저 조회한 나라가 카테고리 몫을 독식하지 않게.
            added_c = 0
            terms = cat["kr"] if country == "KR" else cat["en"]
            for term in terms:
                total_q += 1
                try:
                    items = _fetch(term, country, cat.get("cpc", ""))
                except Exception as e:
                    errors += 1
                    print(f"  ! [{cat['name']}/{country}] '{term}' 실패: {e}")
                    continue
                for it in items:
                    key = _dedup_key(it)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    it["category"] = cat["key"]
                    collected.append(it)
                    added_c += 1
                    cat_added += 1
                    if added_c >= cfg.PER_COUNTRY_LIMIT:
                        break
                if added_c >= cfg.PER_COUNTRY_LIMIT:
                    break
        samp = [c["title"][:34] for c in collected if c["category"] == cat["key"]][:3]
        print(f"  · {cat['emoji']} {cat['name']}: {cat_added}건 | " + " / ".join(samp))
    if not collected and errors >= total_q:
        raise RuntimeError("모든 특허 쿼리 실패(차단/오프라인 추정)")
    return collected


# ── MOCK (오프라인/차단 시 폴백) ──────────────────────────────────
_MOCK = {
    "supply": [("전력 수요 예측 기반 부하 분산 제어 장치 및 방법", "가상전력연구원", "KR"),
               ("Demand response controller for grid load balancing", "MockGrid Inc.", "US")],
    "grid": [("초고압 직류 송전용 전력변환 장치", "가상중공업", "KR"),
             ("Fault detection method for power substation", "MockPower Corp.", "US")],
    "nuclear": [("소형모듈원자로의 피동 냉각 계통", "가상원자력", "KR"),
                ("Passive cooling system for small modular reactor", "MockNuclear LLC", "US")],
    "renew": [("리튬이온 에너지저장장치의 열관리 시스템", "가상배터리", "KR"),
              ("Grid-tied photovoltaic inverter control", "MockSolar Inc.", "US")],
    "datacenter": [("데이터센터용 무정전 전원공급 장치", "가상전자", "KR"),
                   ("Power distribution unit for data center racks", "MockDC Systems", "US")],
    "mega": [("전력반도체 모듈의 방열 구조", "가상반도체", "KR"),
             ("Silicon carbide power semiconductor device", "MockSemi Ltd.", "US")],
    "meter": [("양방향 스마트 전력량계 및 통신 방법", "가상계량", "KR"),
              ("Smart metering system with anomaly detection", "MockMeter Co.", "US")],
    "industry": [("가스절연 개폐장치용 차단기", "가상전기", "KR"),
                 ("High voltage circuit breaker assembly", "MockSwitch Corp.", "US")],
}


def _mock_collect(today: datetime) -> list[dict]:
    seed = int(hashlib.md5(today.strftime("%Y-%m-%d").encode()).hexdigest()[:8], 16)
    collected = []
    for ci, cat in enumerate(cfg.CATEGORIES):
        for i, (title, assignee, country) in enumerate(_MOCK.get(cat["key"], [])):
            days_ago = (seed + ci * 2 + i * 3) % max(1, cfg.LOOKBACK_DAYS)
            pub = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            serial = 10000 + (seed + ci * 37 + i * 101) % 90000
            num = (f"KR1020260{serial}A" if country == "KR"
                   else f"US2026{serial}A1")
            collected.append({
                "number": num,
                "title": title,
                "assignee": assignee,
                "inventor": "",
                "pub_date": pub,
                "filing_date": None,
                "snippet": "[샘플 데이터] 네트워크 차단/오프라인 환경의 미리보기용 항목입니다.",
                "country": country,
                "url": "https://patents.google.com/?q=" + urllib.parse.quote(title),
                "category": cat["key"],
            })
    return collected


def collect(today: datetime) -> tuple[list[dict], bool]:
    """최신 공개 특허(키워드별 최신순 상위 N)와 mock 여부를 반환.

    PATENT_MOCK=on → mock / off → 라이브(실패 시 예외) / auto → 라이브 후 실패 시 mock.
    """
    if cfg.is_mock():
        return _mock_collect(today), True
    try:
        return _live_collect(), False
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ 특허 라이브 수집 실패 → MOCK 폴백: {e}")
        return _mock_collect(today), True
