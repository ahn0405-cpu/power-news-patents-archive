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
        # 같은 폴더의 목록(index)·집계(stats) 파일은 주별 버킷이 아니다.
        if f.name in ("index.json", STATS_FILE):
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
        # mock 여부는 항목별로 남긴다. 같은 주에 MOCK 폴백과 라이브 수집이 섞이면
        # 주 단위 플래그로는 실데이터에 '샘플' 배지가 붙거나 그 반대가 된다.
        if mock:
            p["mock"] = True
        else:
            p.pop("mock", None)
        week["patents"].append(p)
        added += 1
    # 주 단위 플래그는 '이 주에 샘플이 하나라도 섞였는가'로만 쓴다(하위호환).
    week["mock"] = any(x.get("mock") for x in week["patents"])
    # 출원인별 '실제 전체 건수'와 '특허청별 건수'(수집 상한과 무관). 저장 목록은
    # 표본이지만 이 값들로 규모 비교·랭킹을 정확히 할 수 있다. 매주 최신값으로 갱신.
    # 집계(totals/offices)는 주별 버킷이 아니라 stats.json 에 따로 누적한다
    # (매일 조금씩 갱신하므로 '수집한 주'와 묶으면 관리가 어긋난다) → save_stats 참조.
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


# ── 출원인 집계(총 건수·특허청별) — 주별 버킷과 분리해 누적 ──────────
# OPS 무료 쿼터 때문에 한 번에 전 출원인을 조회할 수 없어, 매일 일부만 갱신하고
# 여기에 병합해 쌓는다. 덮어쓰지 않는 것이 핵심(부분집합 실행이 이전 값을 지우면 안 됨).
STATS_FILE = "stats.json"


def load_stats(source_dir: Path) -> dict:
    f = _data_dir(Path(source_dir)) / STATS_FILE
    if not f.exists():
        return {"totals": {}, "offices": {}, "updated": {}}
    try:
        obj = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[경고] 특허 집계 읽기 실패: {e}")
        return {"totals": {}, "offices": {}, "updated": {}}
    obj.setdefault("totals", {})
    obj.setdefault("offices", {})
    obj.setdefault("updated", {})
    return obj


def seed_stats(store: dict, weeks: dict[str, dict]) -> int:
    """집계를 주별 버킷에 담던 시절 데이터를 stats.json 으로 한 번 옮긴다(하위호환).

    stats.json 이 이미 있으면 아무것도 하지 않는다. 반환: 옮긴 출원인 수.
    """
    if store.get("totals") or store.get("offices"):
        return 0
    for wk in sorted(weeks):                     # 최신 주 값이 이기도록 오름차순
        for k, v in (weeks[wk].get("totals") or {}).items():
            store["totals"][k] = v
        for k, v in (weeks[wk].get("offices") or {}).items():
            store["offices"][k] = v
    return len(store["totals"])


def merge_stats(store: dict, fresh: dict | None, today: str = "") -> int:
    """새로 받은 집계를 병합. 반환: 갱신된 출원인 수."""
    if not fresh:
        return 0
    n = 0
    for name, v in (fresh.get("totals") or {}).items():
        store["totals"][name] = v
        if today:
            store["updated"][name] = today
        n += 1
    for name, per in (fresh.get("offices") or {}).items():
        store["offices"].setdefault(name, {}).update(per)
    return n


def save_stats(site_dir: Path, store: dict) -> None:
    d = _data_dir(Path(site_dir))
    d.mkdir(parents=True, exist_ok=True)
    (d / STATS_FILE).write_text(
        json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
