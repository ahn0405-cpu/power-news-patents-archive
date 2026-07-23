"""전력 특허 아카이브 저장소 (주 단위).

주(월요일 시작)별 JSON(data/patents/YYYY-MM-DD.json)으로 누적하고 목록
(data/patents/index.json)을 유지한다. 신규 특허는 '지금까지 아카이브된 모든 특허'와
공개번호 기준으로 중복 제거 → 매주 새로 발견된 것만 그 주 항목으로 쌓인다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import patent_config as cfg


def week_start(day: datetime) -> str:
    """그 날이 속한 주의 월요일(YYYY-MM-DD)."""
    monday = day - timedelta(days=day.weekday())
    return monday.strftime("%Y-%m-%d")


def _data_dir(base: Path) -> Path:
    return base / cfg.PATENT_DATA_SUBDIR


def _key(p: dict) -> str:
    return (p.get("number") or "").upper() or re.sub(r"[\s\W_]+", "", p.get("title", "").lower())


def load_weeks(source_dir: Path) -> dict[str, dict]:
    weeks: dict[str, dict] = {}
    ddir = _data_dir(source_dir)
    if not ddir.exists():
        return weeks
    for f in ddir.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            weeks[obj.get("week") or f.stem] = obj
        except Exception as e:
            print(f"[경고] 특허 아카이브 읽기 실패 {f.name}: {e}")
    return weeks


def merge_week(weeks: dict[str, dict], wk: str, fresh: list[dict],
               mock: bool) -> tuple[dict, int]:
    prior = {k for w, wobj in weeks.items() if w != wk
             for p in wobj.get("patents", []) for k in (_key(p),) if k}
    week = weeks.get(wk, {"week": wk, "patents": []})
    have = {_key(p) for p in week.get("patents", [])}
    added = 0
    for p in fresh:
        k = _key(p)
        if not k or k in prior or k in have:
            continue
        have.add(k)
        week["patents"].append(p)
        added += 1
    week["mock"] = mock
    weeks[wk] = week
    return week, added


def save(site_dir: Path, weeks: dict[str, dict], generated: str) -> None:
    ddir = _data_dir(site_dir)
    ddir.mkdir(parents=True, exist_ok=True)
    for wk, wobj in weeks.items():
        (ddir / f"{wk}.json").write_text(
            json.dumps(wobj, ensure_ascii=False, indent=2), encoding="utf-8")
    index = []
    for wk in sorted(weeks, reverse=True):
        pats = weeks[wk].get("patents", [])
        by_cat: dict[str, int] = {}
        by_country: dict[str, int] = {}
        for p in pats:
            by_cat[p.get("category", "etc")] = by_cat.get(p.get("category", "etc"), 0) + 1
            by_country[p.get("country", "?")] = by_country.get(p.get("country", "?"), 0) + 1
        index.append({"week": wk, "count": len(pats),
                      "by_category": by_cat, "by_country": by_country})
    (ddir / "index.json").write_text(
        json.dumps({"generated": generated, "weeks": index},
                   ensure_ascii=False, indent=2), encoding="utf-8")
