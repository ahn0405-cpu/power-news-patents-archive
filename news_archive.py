"""전력 뉴스 아카이브 저장소.

날짜별 JSON(data/YYYY-MM-DD.json)으로 누적하고, 목록(data/index.json)을 유지한다.
새 기사는 '지금까지 아카이브된 모든 기사'와 제목 기준으로 중복 제거 → 매일
새로 발견된 것만 그날 항목으로 쌓인다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import news_config as cfg


def _data_dir(site_dir: Path) -> Path:
    return site_dir / cfg.DATA_SUBDIR


def _norm_key(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", (title or "").lower())


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

    today = days.get(date, {"date": date, "articles": []})
    today_keys = existing_keys({date: today})
    added = 0
    for art in fresh:
        key = _norm_key(art.get("title", ""))
        if not key or key in prior or key in today_keys:
            continue
        today_keys.add(key)
        today["articles"].append(art)
        added += 1
    today["mock"] = mock
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
