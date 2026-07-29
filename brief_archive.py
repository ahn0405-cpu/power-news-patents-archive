"""서술형 브리핑 아카이브(누적).

저장소 루트의 brief.json(뉴스, 매일)·patent_brief.json(특허, 매주)은 '최신 1개'만
담고 덮어써진다(반자동 Routine). 그걸 날짜/주차별로 보존해 '최신 + 지난 브리핑
타임라인'을 만든다.

동작: 이전 아카이브(gh-pages 복원분)를 읽어 현재 브리핑을 키로 병합 →
site/data/briefs/<date>.json, site/data/patent_briefs/<week>.json 로 다시 저장.

뉴스는 날짜(date), 특허는 수집 주(week)가 키다. 나머지 동작은 같아서 한 모듈이
subdir/key 만 바꿔 양쪽을 처리한다.
"""
from __future__ import annotations

import json
from pathlib import Path

BRIEF_SUBDIR = "data/briefs"
PATENT_BRIEF_SUBDIR = "data/patent_briefs"


def _dir(base: Path, subdir: str = BRIEF_SUBDIR) -> Path:
    return Path(base) / subdir


def load_briefs(source_dir: Path, subdir: str = BRIEF_SUBDIR,
                key: str = "date") -> dict[str, dict]:
    """source_dir/<subdir>/*.json → {키: brief}."""
    out: dict[str, dict] = {}
    d = _dir(source_dir, subdir)
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
            k = obj.get(key) or f.stem
            if k:
                out[k] = obj
        except Exception as e:
            print(f"[경고] 브리핑 아카이브 읽기 실패 {f.name}: {e}")
    return out


def merge(briefs: dict[str, dict], current: dict | None, key: str = "date") -> None:
    """현재 브리핑을 키로 추가/갱신(같은 키면 최신 내용으로 교체)."""
    if current and current.get(key) and (current.get("headline") or current.get("body")):
        briefs[current[key]] = current


def save(site_dir: Path, briefs: dict[str, dict], subdir: str = BRIEF_SUBDIR) -> None:
    d = _dir(site_dir, subdir)
    d.mkdir(parents=True, exist_ok=True)
    for k, b in briefs.items():
        (d / f"{k}.json").write_text(
            json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")


def sorted_list(briefs: dict[str, dict]) -> list[dict]:
    """최신순 리스트."""
    return [briefs[k] for k in sorted(briefs, reverse=True)]
