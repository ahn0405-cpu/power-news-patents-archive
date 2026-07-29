"""전력 뉴스 아카이브 저장소.

날짜별 JSON(data/YYYY-MM-DD.json)으로 누적하고, 목록(data/index.json)을 유지한다.
새 기사는 '지금까지 아카이브된 기사'와 중복 제거 → 매일 새로 발견된 것만 그날 항목으로
쌓인다. 중복 판정은 두 단계다:
  1) 제목 완전일치(또는 URL 동일)
  2) 제목 2-gram 자카드 유사도 — 같은 사건을 다른 매체가 며칠에 걸쳐 조금씩 다른
     제목으로 내보내는 경우를 잡는다(수집 단계의 판정과 같은 기준).
2)는 최근 DEDUP_WINDOW_DAYS 일치 기사와만 비교해 아카이브가 커져도 비용이 일정하다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import news_config as cfg

# 유사 판정 비교 창(일). 오래된 기사와는 비교하지 않는다(같은 사건이 몇 주 뒤
# 재등장하면 그건 새 소식일 가능성이 크고, 비용도 아카이브 크기와 무관해진다).
DEDUP_WINDOW_DAYS = 30


def _data_dir(site_dir: Path) -> Path:
    return site_dir / cfg.DATA_SUBDIR


def _norm_key(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", (title or "").lower())


def bigrams(title: str) -> set[str]:
    """제목의 문자 2-gram 집합(공백·기호 제거). 유사 기사 판정용."""
    s = re.sub(r"[^0-9a-z가-힣]+", "", (title or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 2 else {s}


def similar(a: set[str], b: set[str]) -> float:
    """두 2-gram 집합의 자카드 유사도."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _within_window(date: str, ref: str) -> bool:
    """date 가 ref 기준 최근 DEDUP_WINDOW_DAYS 안인가(파싱 실패 시 포함)."""
    try:
        d = datetime.strptime(date[:10], "%Y-%m-%d")
        r = datetime.strptime(ref[:10], "%Y-%m-%d")
    except Exception:
        return True
    return (r - d) <= timedelta(days=DEDUP_WINDOW_DAYS)


def load_days(source_dir: Path) -> dict[str, dict]:
    """source_dir/data/*.json 을 모두 읽어 {date: day_dict} 로 반환."""
    days: dict[str, dict] = {}
    ddir = _data_dir(source_dir)
    if not ddir.exists():
        return days
    for f in ddir.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            date = obj.get("date") or f.stem
            days[date] = obj
        except Exception as e:
            print(f"[경고] 아카이브 읽기 실패 {f.name}: {e}")
    return days


def existing_keys(days: dict[str, dict]) -> set[str]:
    keys: set[str] = set()
    for day in days.values():
        for art in day.get("articles", []):
            keys.add(_norm_key(art.get("title", "")))
            u = art.get("url")
            if u:
                keys.add(u)
    return keys


def merge_today(days: dict[str, dict], date: str, fresh: list[dict],
                mock: bool) -> tuple[dict, int]:
    """오늘(date) 항목을 만들거나 갱신. 이미 있던 기사는 제외하고 신규만 추가.

    반환: (오늘 day dict, 새로 추가된 건수)
    """
    prior = {k for d, day in days.items() if d != date
             for art in day.get("articles", [])
             for k in (_norm_key(art.get("title", "")), art.get("url"))
             if k}

    # 유사 판정용: 최근 창 안의 기존 제목 2-gram (오늘 것 포함해 누적)
    recent_bg = [bigrams(art.get("title", ""))
                 for d, day in days.items() if _within_window(d, date)
                 for art in day.get("articles", [])]

    today = days.get(date, {"date": date, "articles": []})
    today_keys = existing_keys({date: today})
    added = 0
    for art in fresh:
        key = _norm_key(art.get("title", ""))
        url = art.get("url")
        # 제목 완전일치와 URL 동일을 모두 본다. URL 검사가 빠져 있어 같은 기사가
        # 제목만 바뀌어 다시 들어오던 문제가 있었다(prior 에 URL 을 담아두고도
        # 조회하지 않았다).
        if not key or key in prior or key in today_keys:
            continue
        if url and (url in prior or url in today_keys):
            continue
        # 같은 사건의 다른 매체·다른 날 기사(제목만 조금 다른 경우) 제외
        bg = bigrams(art.get("title", ""))
        if any(similar(bg, k) >= cfg.DEDUP_SIM for k in recent_bg):
            continue
        recent_bg.append(bg)
        today_keys.add(key)
        if url:
            today_keys.add(url)
        # mock 여부는 항목별로 남긴다. 하루 안에 라이브 수집과 MOCK 폴백이 섞이면
        # 날짜 단위 플래그로는 실데이터에 '샘플' 배지가 붙거나 그 반대가 된다
        # (특허 쪽 merge_week 와 같은 방식).
        if mock:
            art["mock"] = True
        else:
            art.pop("mock", None)
        today["articles"].append(art)
        added += 1
    # 날짜 단위 플래그는 '이 날 샘플이 하나라도 섞였는가'로만 쓴다(하위호환).
    today["mock"] = any(a.get("mock") for a in today["articles"])
    days[date] = today
    return today, added


def save(site_dir: Path, days: dict[str, dict], generated: str) -> None:
    ddir = _data_dir(site_dir)
    ddir.mkdir(parents=True, exist_ok=True)
    # 날짜별 파일
    for date, day in days.items():
        (ddir / f"{date}.json").write_text(
            json.dumps(day, ensure_ascii=False, indent=2), encoding="utf-8")
    # 목록(index.json): 날짜·건수·카테고리별 건수
    index = []
    for date in sorted(days, reverse=True):
        arts = days[date].get("articles", [])
        by_cat: dict[str, int] = {}
        for a in arts:
            by_cat[a.get("category", "etc")] = by_cat.get(a.get("category", "etc"), 0) + 1
        index.append({"date": date, "count": len(arts), "by_category": by_cat})
    (ddir / "index.json").write_text(
        json.dumps({"generated": generated, "days": index},
                   ensure_ascii=False, indent=2), encoding="utf-8")
