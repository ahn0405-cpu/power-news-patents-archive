"""서술형 브리핑 아카이브(누적).

저장소 루트의 brief.json 은 '최신 1개'만 담고 매주 덮어써진다(반자동 Routine).
그걸 날짜별로 보존해 홈 대시보드에 '최신 + 지난 브리핑 타임라인'을 만든다.

동작: 이전 아카이브(gh-pages 복원분)를 읽어 현재 brief.json 을 날짜 키로 병합 →
site/data/briefs/<date>.json 로 다시 저장(뉴스·특허처럼 누적). 매주 다른 날짜의
브리핑이 쌓인다.
"""
from __future__ import annotations

import json
from pathlib import Path

BRIEF_SUBDIR = "data/briefs"


def _dir(base: Path) -> Path:
    return Path(base) / BRIEF_SUBDIR


def load_briefs(source_dir: Path) -> dict[str, dict]:
    """source_dir/data/briefs/*.json → {date: brief}."""
    out: dict[str, dict] = {}
    d = _dir(source_dir)
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            date = obj.get("date") or f.stem
            if date:
                out[date] = obj
        except Exception as e:
            print(f"[경고] 브리핑 아카이브 읽기 실패 {f.name}: {e}")
    return out


def merge(briefs: dict[str, dict], current: dict | None) -> None:
    """현재 brief.json 을 날짜 키로 추가/갱신(같은 날짜면 최신 내용으로 교체)."""
    if current and current.get("date") and (current.get("headline") or current.get("body")):
        briefs[current["date"]] = current


def save(site_dir: Path, briefs: dict[str, dict]) -> None:
    d = _dir(site_dir)
    d.mkdir(parents=True, exist_ok=True)
    for date, b in briefs.items():
        (d / f"{date}.json").write_text(
            json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")


def sorted_list(briefs: dict[str, dict]) -> list[dict]:
    """최신순 리스트."""
    return [briefs[k] for k in sorted(briefs, reverse=True)]
