"""전력 뉴스 수집 (Google 뉴스 RSS, 무API·무키).

카테고리별로 RSS 를 한 번씩 조회 → 기사(title/url/source/published/summary)를
표준 dict 로 반환한다. 표준 라이브러리(urllib+xml)만 쓴다.

네트워크가 막히거나(사내 프록시·오프라인) NEWS_MOCK=on 이면 재현 가능한 합성
헤드라인으로 폴백한다. 덕분에 로컬에서도 사이트가 항상 빌드된다.
(실데이터 수집은 GitHub Actions 러너에서 정상 동작한다.)
"""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import news_config as cfg

_UA = "Mozilla/5.0 (compatible; PowerNewsArchive/1.0; +https://github.com)"
_RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"

# RFC822 날짜(Wed, 22 Jul 2026 09:00:00 GMT) 파싱용 월 매핑
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _parse_pubdate(s: str) -> str | None:
    """RFC822 → ISO8601(UTC). 실패하면 None."""
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    day, mon, year, hh, mm, ss = m.groups()
    if mon not in _MONTHS:
        return None
    try:
        dt = datetime(int(year), _MONTHS[mon], int(day),
                      int(hh), int(mm), int(ss), tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _split_title_source(title: str) -> tuple[str, str]:
    """구글 뉴스 제목은 보통 '헤드라인 - 언론사' 형태. 언론사를 분리한다."""
    if " - " in title:
        head, _, src = title.rpartition(" - ")
        if head and len(src) <= 25:
            return head.strip(), src.strip()
    return title.strip(), ""


def _fetch_rss(query: str) -> bytes:
    url = _RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=cfg.REQUEST_TIMEOUT) as r:
        return r.read()


def _parse_items(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iterfind(".//item"):
        title_raw = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title_raw or not link:
            continue
        title, src_from_title = _split_title_source(title_raw)
        source_el = it.find("source")
        src_tag = (source_el.text.strip() if source_el is not None and source_el.text else "")
        # <source> 가 포털 도메인(v.daum.net 등)이면 제목 꼬리의 실제 매체명을 우선.
        if _looks_like_domain(src_tag) and src_from_title:
            source = src_from_title
        else:
            source = src_tag or src_from_title
        items.append({
            "title": title,
            "url": link,
            "source": source,
            "published": _parse_pubdate(it.findtext("pubDate") or ""),
            "summary": _strip_tags(it.findtext("description") or "")[:220],
        })
    return items


def _norm_key(title: str) -> str:
    """중복 판정 키: 제목에서 공백·기호 제거 후 소문자."""
    return re.sub(r"[\s\W_]+", "", title.lower())


def _looks_like_domain(s: str) -> bool:
    return bool(s) and " " not in s and bool(re.match(r"^[\w.-]+\.[a-z]{2,}$", s.lower()))


def _bigrams(title: str) -> set[str]:
    """제목의 문자 2-gram 집합(공백·기호 제거). 유사 기사 판정용."""
    s = re.sub(r"[^0-9a-z가-힣]+", "", title.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 2 else {s}


def _similar(a: set[str], b: set[str]) -> float:
    """두 2-gram 집합의 자카드 유사도."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _live_collect() -> list[dict]:
    """카테고리별 RSS 수집 → 카테고리 태깅 + 카테고리 내 중복 제거."""
    collected: list[dict] = []
    seen: set[str] = set()
    kept_bg: list[set[str]] = []   # 지금까지 채택한 제목의 2-gram (유사 기사 판정용)
    errors = 0
    for cat in cfg.CATEGORIES:
        query = " OR ".join(f'"{q}"' for q in cat["queries"])
        try:
            items = _parse_items(_fetch_rss(query))
        except Exception as e:  # 개별 카테고리 실패는 건너뛴다
            errors += 1
            print(f"  ! [{cat['name']}] 수집 실패: {e}")
            continue
        added = 0
        for it in items:
            key = _norm_key(it["title"])
            if not key or key in seen:
                continue
            # 같은 사건을 다른 매체가 조금씩 다른 제목으로 낸 경우(유사도) 제외.
            bg = _bigrams(it["title"])
            if any(_similar(bg, k) >= cfg.DEDUP_SIM for k in kept_bg):
                continue
            seen.add(key)
            kept_bg.append(bg)
            it["category"] = cat["key"]
            collected.append(it)
            added += 1
            if added >= cfg.PER_CATEGORY_LIMIT:
                break
        print(f"  · {cat['emoji']} {cat['name']}: {added}건")
    if not collected and errors == len(cfg.CATEGORIES):
        raise RuntimeError("모든 카테고리 수집 실패(네트워크 차단 추정)")
    return collected


# ── MOCK (오프라인/차단 시 폴백) ──────────────────────────────────
_MOCK_HEADLINES = {
    "supply": [
        ("올여름 최대전력 수요 사상 최고치 경신…예비율 한 자릿수", "가상경제"),
        ("폭염에 전력수급 비상…정부 수요관리 발동 검토", "가상일보"),
    ],
    "grid": [
        ("동해안∼수도권 HVDC 송전선로 건설 지연…주민 협의 난항", "가상전력신문"),
        ("변전소 증설 두고 지자체·한전 갈등…전력망 확충 과제", "가상뉴스"),
    ],
    "nuclear": [
        ("신한울 3·4호기 건설 재개 속도…원전 확대 정책 탄력", "가상에너지"),
        ("SMR 소형모듈원전 실증 부지 선정 논의 본격화", "가상사이언스"),
    ],
    "renew": [
        ("서해안 해상풍력 대규모 단지 인허가 급물살", "가상그린"),
        ("재생에너지 계통 접속 대기 급증…ESS 확충 목소리", "가상경제"),
    ],
    "datacenter": [
        ("AI 붐에 데이터센터 전력 수요 폭증…수도권 집중 논란", "가상IT"),
        ("데이터센터 전력난 우려에 지방 분산 유인책 검토", "가상일보"),
    ],
    "mega": [
        ("용인 반도체 클러스터 전력 공급 로드맵 확정", "가상산업"),
        ("국가첨단전략산업 특화단지, 전력·용수 인프라가 관건", "가상경제"),
    ],
    "policy": [
        ("전기요금 인상 여부 논의…한전 누적적자 부담 여전", "가상파이낸스"),
        ("제11차 전력수급기본계획 확정…원전·재생 균형 초점", "가상정책"),
    ],
    "industry": [
        ("초고압 케이블 수출 호조…전선업계 사상 최대 수주", "가상산업"),
        ("변압기 품귀에 납기 지연…전력기기 슈퍼사이클 지속", "가상비즈"),
    ],
}


def _mock_collect(today: datetime) -> list[dict]:
    seed = int(hashlib.md5(today.strftime("%Y-%m-%d").encode()).hexdigest()[:8], 16)
    collected = []
    for cat in cfg.CATEGORIES:
        for i, (title, src) in enumerate(_MOCK_HEADLINES.get(cat["key"], [])):
            # 날짜 시드로 살짝 흔들어 매일 조금씩 달라 보이게(재현 가능)
            hrs = (seed + i * 7) % 24
            pub = (today - timedelta(hours=hrs)).replace(microsecond=0)
            collected.append({
                "title": title,
                "url": "https://news.google.com/search?q=" + urllib.parse.quote(title),
                "source": src,
                "published": pub.astimezone(timezone.utc).isoformat(),
                "summary": "[샘플 데이터] 네트워크 차단/오프라인 환경의 미리보기용 항목입니다.",
                "category": cat["key"],
            })
    return collected


def collect(today: datetime) -> tuple[list[dict], bool]:
    """오늘 수집된 기사 목록과 mock 여부를 반환.

    NEWS_MOCK=on  → 무조건 mock
    NEWS_MOCK=off → 무조건 라이브(실패 시 예외)
    auto          → 라이브 시도, 실패하면 mock 폴백
    """
    if cfg.is_mock():
        return _mock_collect(today), True
    try:
        return _live_collect(), False
    except Exception as e:
        if cfg.force_live():
            raise
        print(f"⚠️ 라이브 수집 실패 → MOCK 폴백: {e}")
        return _mock_collect(today), True
