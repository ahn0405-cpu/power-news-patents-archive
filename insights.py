"""전력 이슈 아카이브 — 인사이트(트렌드) 계산. 무API·무키·완전자동.

뉴스/특허 아카이브(이미 수집·정규화된 dict)만 입력으로 받아, 사이트 상단에 노출할
'요약 통계'를 만든다. LLM 없이 순수 계산으로만 뽑는 사실 지표라서 매일 GitHub
Actions 에서 그대로 재생성된다. (서술형 브리핑을 나중에 얹더라도 이 지표가 뼈대다.)

산출물(dict):
  asOf       : 기준일(최신 뉴스 날짜)
  window     : {recentDays, priorDays}
  trending   : 최근 N일 많이 언급된 키워드 [{term,count,prev,rising}]
  catTrend   : 카테고리별 최근 vs 이전 건수 [{key,name,emoji,recent,prev,delta}]

여기서는 뉴스만 집계한다. 특허는 주 1회 수집이라 '일별 트렌드'와 축이 다르고,
사이트는 특허를 별도 매트릭스/하이라이트로 보여준다.

수치는 여기서만 계산하고, 서술(자연어)은 하지 않는다 — 역할 분리(코드=계산).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import news_config as ncfg

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
    # '소형모듈원자로'처럼 뒤에 말이 붙는 형태까지 대표어로 흡수한다.
    ("SMR", r"\bSMR\b|소형모듈[가-힣]*"), ("HVDC", r"\bHVDC\b"), ("ESS", r"\bESS\b"),
    ("AI", r"\bAI\b|인공지능"), ("변압기", r"변압기"), ("전력난", r"전력\s*난"),
]
_HANGUL = re.compile(r"[가-힣]")
# 기사마다 도는 경로라 패턴은 미리 컴파일해 둔다(제목 수 × 표현 수만큼 호출된다).
_PHRASE_RX = [(canon, re.compile(pat, re.I)) for canon, pat in _PHRASES]


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
    """한 기사에서 뽑은 (중복 없는) 키워드 집합. 기사당 1회만 세도록 set 반환.

    _PHRASES 가 대표어로 묶은 표현은 낱개 토큰 쪽에서 지운다. 안 그러면 같은 말이
    두 칩으로 갈라진다 — '한국전력'(대표어)과 '한전'(토큰), '원전'과 '원자력',
    'AI'와 '인공지능' 이 각각 따로 집계되던 문제.
    """
    toks = set(_tokens(title))
    hits = _phrase_hits(title)
    if hits:
        for canon, rx in _PHRASE_RX:
            if canon in hits:
                toks = {t for t in toks if not rx.fullmatch(t)}
    return toks | hits


def _iter_articles(news_days: dict):
    for date in sorted(news_days):
        d = _to_date(date)
        for a in news_days[date].get("articles", []):
            yield d, a


# 최근 창은 7일, 이전 창은 21일이다. 건수를 그대로 비교하면 3배 긴 창이 당연히 크게
# 나와 무엇이든 '감소'로 보인다 → 창마다 **기사가 있는 날 수**로 나눠 하루 평균으로
# 비교한다. 명목 일수(7·21)가 아니라 실제로 기사가 있는 날로 나누는 이유는, 아카이브를
# 막 시작하면 이전 창이 부분적으로만 차 있기 때문이다(명목으로 나누면 이전 값이 실제보다
# 작아져 이번엔 반대로 전부 '증가'가 된다).
def _obs_days(news_days: dict, lo, hi) -> int:
    """[lo, hi] 안에서 기사가 하나라도 있는 날 수."""
    n = 0
    for date, day in news_days.items():
        d = _to_date(date)
        if d and lo <= d <= hi and day.get("articles"):
            n += 1
    return n


RISE_RATIO = 1.10        # 하루 평균이 이만큼 넘어야 '늘었다'(표본이 작아 ±10%는 잡음)


def _rate(count: int, days: int) -> float:
    return count / days if days else 0.0


def _trending(news_days: dict, recent_from, prior_from, r_days, p_days):
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
        rr, pr = _rate(c, r_days), _rate(p, p_days)
        rows.append({"term": t, "count": c, "prev": p,
                     "rate": round(rr, 3), "prevRate": round(pr, 3),
                     "rising": rr > pr * RISE_RATIO})
    # 최근 언급 많은 순, 동률이면 하루 평균 상승폭 큰 순
    rows.sort(key=lambda r: (r["count"], r["rate"] - r["prevRate"]), reverse=True)
    return rows[:TOP_KEYWORDS]


def _cat_trend(news_days: dict, recent_from, prior_from, r_days, p_days):
    recent, prior = {}, {}
    for d, a in _iter_articles(news_days):
        if d is None:
            continue
        k = a.get("category", "etc")
        if d >= recent_from:
            recent[k] = recent.get(k, 0) + 1
        elif d >= prior_from:
            prior[k] = prior.get(k, 0) + 1
    # 방향 판정은 '하루 평균 건수'가 아니라 **그날 기사에서 차지한 비중**으로 한다.
    # 수집은 아카이브 전체와 중복 제거를 하므로, 아카이브가 커질수록 '새 기사'로
    # 잡히는 수가 구조적으로 줄어든다 — 실측(8/5)에서 전 분야가 예외 없이 감소로
    # 나왔는데, 전체가 73건/일 → 56건/일 로 준 탓이었다. 비중으로 보면 이 흐름이
    # 상쇄돼 분야끼리의 실제 이동(재생에너지·전력수급 ↑, 데이터센터·메가 ↓)이 드러난다.
    r_tot, p_tot = sum(recent.values()), sum(prior.values())
    rows = []
    for c in ncfg.CATEGORIES:
        k = c["key"]
        r, p = recent.get(k, 0), prior.get(k, 0)
        if r == 0 and p == 0:
            continue
        rr, pr = _rate(r, r_days), _rate(p, p_days)
        rs = r / r_tot if r_tot else 0.0
        ps = p / p_tot if p_tot else 0.0
        rows.append({"key": k, "name": c["name"], "emoji": c["emoji"],
                     "recent": r, "prev": p, "delta": r - p,
                     "rate": round(rr, 3), "prevRate": round(pr, 3),
                     "share": round(rs, 4), "prevShare": round(ps, 4),
                     # 비중 배율. 이전 비중이 0이면 정의되지 않아 None.
                     "ratio": round(rs / ps, 3) if ps else None})
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
    # 아카이브를 막 시작했으면 '이전' 창이 비어 prev 가 전부 0 이 된다. 그대로 두면
    # 모든 키워드에 증가 표시가 붙어 전부 급증한 것처럼 보인다 → 비교 가능 여부를
    # 함께 내보내고 화면에서 증감 표시를 감춘다.
    prior_n = sum(1 for d, _a in _iter_articles(news_days)
                  if d is not None and prior_from <= d < recent_from)
    r_days = _obs_days(news_days, recent_from, latest)
    p_days = _obs_days(news_days, prior_from, recent_from - timedelta(days=1))
    return {
        "asOf": latest.isoformat(),
        # recentDays/priorDays 는 창의 명목 길이, obs 는 실제로 기사가 있던 날 수다.
        # 화면에서 '하루 평균' 이라고 쓸 때 근거가 되는 건 obs 쪽이다.
        "window": {"recentDays": RECENT_DAYS, "priorDays": PRIOR_DAYS,
                   "recentObs": r_days, "priorObs": p_days},
        "priorArticles": prior_n,
        "comparable": prior_n > 0 and p_days > 0,
        "trending": _trending(news_days, recent_from, prior_from, r_days, p_days),
        "catTrend": _cat_trend(news_days, recent_from, prior_from, r_days, p_days),
    }
