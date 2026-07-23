"""전력 이슈 아카이브 — 인사이트(트렌드) 계산. 무API·무키·완전자동.

뉴스/특허 아카이브(이미 수집·정규화된 dict)만 입력으로 받아, 사이트 상단에 노출할
'요약 통계'를 만든다. LLM 없이 순수 계산으로만 뽑는 사실 지표라서 매일 GitHub
Actions 에서 그대로 재생성된다. (서술형 브리핑을 나중에 얹더라도 이 지표가 뼈대다.)

산출물(dict):
  asOf       : 기준일(최신 뉴스 날짜)
  window     : {recentDays, priorDays, recentWeeks}
  trending   : 최근 N일 많이 언급된 키워드 [{term,count,prev,rising}]
  catTrend   : 카테고리별 최근 vs 이전 건수 [{key,name,emoji,recent,prev,delta}]

특허는 '건수'로 다루지 않는다: 수집이 카테고리·국가별 상한(국가당 PER_COUNTRY_LIMIT)에서
잘려 대부분 상한값으로 채워지므로 카테고리 간 특허 건수 비교는 통계적 의미가 없다.
(사이트는 특허를 '이번 주 공개 특허 하이라이트'처럼 질적으로만 상단에 노출한다.)

수치는 여기서만 계산하고, 서술(자연어)은 하지 않는다 — 역할 분리(코드=계산).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import news_config as ncfg
import patent_config as pcfg

RECENT_DAYS = 7           # '최근' 창(일)
PRIOR_DAYS = 21           # 비교 대상 '이전' 창(일)
TOP_KEYWORDS = 12         # 노출할 키워드 상위 수
MIN_KW_COUNT = 2          # 이보다 적게 나온 키워드는 버림(잡음 제거)

# 뉴스 제목에 흔한 일반 어휘(불용어). 도메인 용어(전력난·데이터센터 등)는 남긴다.
_STOP_KO = {
    "관련", "대한", "위한", "위해", "이번", "올해", "내년", "지난해", "지난",
    "오늘", "내일", "우리", "최대", "최고", "사상", "전망", "계획", "추진",
    "확대", "축소", "검토", "방안", "통해", "정부", "관계자", "예정", "국내",
    "세계", "글로벌", "발표", "개최", "논의", "강조", "결정", "시작", "종료",
    "하반기", "상반기", "분기", "그룹", "기업", "산업", "시장", "정책", "문제",
    "이슈", "상황", "가능", "필요", "대응", "본격", "속도", "역대", "주요",
    "공개", "출시", "도입", "구축", "조성", "지원", "협력", "체결", "확보",
}
_STOP_EN = {"the", "and", "for", "with", "from", "you", "are", "new"}
# 제목 안에서 통째로 잡아낼 다어절/특수 도메인 표현(공백·표기 흔들림 흡수).
_PHRASES = [
    ("데이터센터", r"데이터\s*센터"), ("반도체", r"반도체"), ("전력망", r"전력\s*망"),
    ("전기요금", r"전기\s*요금"), ("한국전력", r"한국\s*전력|한전"),
    ("해상풍력", r"해상\s*풍력"), ("태양광", r"태양광"), ("예비율", r"예비율"),
    ("송전", r"송전"), ("변전소", r"변전소"), ("원전", r"원전|원자력"),
    ("SMR", r"\bSMR\b|소형모듈"), ("HVDC", r"\bHVDC\b"), ("ESS", r"\bESS\b"),
    ("AI", r"\bAI\b|인공지능"), ("변압기", r"변압기"), ("전력난", r"전력\s*난"),
]
_HANGUL = re.compile(r"[가-힣]")


def _to_date(s: str):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _tokens(title: str) -> list[str]:
    """제목 → 의미 토큰. 공백/기호로 나눈 뒤 불용어·숫자·1글자 제거.

    한글 토큰은 원형 유지(조사 제거는 오탈락 위험이 커 하지 않는다). 라틴 약어
    (AI·SMR 등)는 대문자로 통일. 표기 흔들리는 도메인 표현은 _PHRASES 로 보정.
    """
    out: list[str] = []
    for m in re.finditer(r"[A-Za-z]{2,}|[가-힣]{2,}", title or ""):
        tok = m.group(0)
        if _HANGUL.search(tok):
            if tok in _STOP_KO:
                continue
            out.append(tok)
        else:
            low = tok.lower()
            if low in _STOP_EN:
                continue
            out.append(tok.upper() if len(tok) <= 5 else low)
    return out


def _phrase_hits(title: str) -> set[str]:
    hits = set()
    for canon, pat in _PHRASES:
        if re.search(pat, title or ""):
            hits.add(canon)
    return hits


def _news_terms(title: str) -> set[str]:
    """한 기사에서 뽑은 (중복 없는) 키워드 집합. 기사당 1회만 세도록 set 반환."""
    return set(_tokens(title)) | _phrase_hits(title)


def _iter_articles(news_days: dict):
    for date in sorted(news_days):
        d = _to_date(date)
        for a in news_days[date].get("articles", []):
            yield d, a


def _trending(news_days: dict, latest, recent_from, prior_from):
    recent, prior = {}, {}
    for d, a in _iter_articles(news_days):
        if d is None:
            continue
        terms = _news_terms(a.get("title", ""))
        if d >= recent_from:
            for t in terms:
                recent[t] = recent.get(t, 0) + 1
        elif d >= prior_from:
            for t in terms:
                prior[t] = prior.get(t, 0) + 1
    rows = []
    for t, c in recent.items():
        if c < MIN_KW_COUNT:
            continue
        p = prior.get(t, 0)
        rows.append({"term": t, "count": c, "prev": p, "rising": c > p})
    # 최근 언급 많은 순, 동률이면 상승폭 큰 순
    rows.sort(key=lambda r: (r["count"], r["count"] - r["prev"]), reverse=True)
    return rows[:TOP_KEYWORDS]


def _cat_trend(news_days: dict, recent_from, prior_from):
    recent, prior = {}, {}
    for d, a in _iter_articles(news_days):
        if d is None:
            continue
        k = a.get("category", "etc")
        if d >= recent_from:
            recent[k] = recent.get(k, 0) + 1
        elif d >= prior_from:
            prior[k] = prior.get(k, 0) + 1
    rows = []
    for c in ncfg.CATEGORIES:
        k = c["key"]
        r, p = recent.get(k, 0), prior.get(k, 0)
        if r == 0 and p == 0:
            continue
        rows.append({"key": k, "name": c["name"], "emoji": c["emoji"],
                     "recent": r, "prev": p, "delta": r - p})
    rows.sort(key=lambda x: (x["recent"], x["delta"]), reverse=True)
    return rows


def build(news_days: dict, patent_weeks: dict) -> dict:
    """뉴스·특허 아카이브 → 인사이트 dict. 데이터가 비면 빈 구조를 돌려준다.

    특허는 여기서 집계하지 않는다(건수는 상한으로 잘려 의미가 없어, 상단엔 질적으로만 노출).
    patent_weeks 인자는 시그니처 호환을 위해 유지."""
    dates = [d for d in (_to_date(x) for x in news_days) if d]
    if not dates:
        return {"asOf": "", "trending": [], "catTrend": [],
                "window": {"recentDays": RECENT_DAYS, "priorDays": PRIOR_DAYS}}
    latest = max(dates)
    recent_from = latest - timedelta(days=RECENT_DAYS - 1)
    prior_from = latest - timedelta(days=RECENT_DAYS - 1 + PRIOR_DAYS)
    return {
        "asOf": latest.isoformat(),
        "window": {"recentDays": RECENT_DAYS, "priorDays": PRIOR_DAYS},
        "trending": _trending(news_days, latest, recent_from, prior_from),
        "catTrend": _cat_trend(news_days, recent_from, prior_from),
    }
