"""전력 이슈 아카이브 — 사이트 빌드 (뉴스 + 특허).

수집기는 유형별로 분리돼 있고, 어느 쪽을 돌리든 항상 두 데이터(뉴스·특허)를
모두 불러와 다시 저장하고 전체 사이트를 재생성한다 → 어떤 워크플로가 돌아도
다른 탭의 데이터가 유실되지 않는다.

사용법:
  python build_site.py                 # (로컬) 뉴스+특허 둘 다 수집 후 빌드
  python build_site.py --collect news  # 뉴스 + 특허 공개국 집계 일부 (매일 워크플로)
  python build_site.py --collect patents  # 특허 목록만 수집 (매주 워크플로)
  python build_site.py --collect offices  # 특허 공개국 집계만 (수동 보충)
  python build_site.py --collect none  # 수집 없이 기존 데이터로 재빌드만

환경변수:
  NEWS_SITE_DIR (기본 site) / NEWS_PREV_DIR (이전 아카이브, Actions=gh-pages 체크아웃)
  NEWS_MOCK / PATENT_MOCK : auto | on | off
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import news_config as ncfg
import news_source
import news_archive
import patent_archive
import patent_source
import brief_archive
import site_render


def _load_brief() -> dict | None:
    """저장소에 커밋된 서술형 브리핑(brief.json)을 읽는다. 반자동(사람이 갱신·커밋)이라
    수집·빌드 자동화와 분리돼 있고, 파일이 없거나 깨지면 조용히 건너뛴다(뉴스는 정상)."""
    f = Path(__file__).resolve().parent / "brief.json"
    if not f.exists():
        return None
    try:
        obj = json.loads(f.read_text(encoding="utf-8"))
        return obj or None
    except Exception as e:
        print(f"[경고] brief.json 읽기 실패(무시): {e}")
        return None


def _collect_arg() -> str:
    argv = sys.argv[1:]
    if "--collect" in argv:
        i = argv.index("--collect")
        if i + 1 < len(argv):
            return argv[i + 1].lower()
    return "both"


def main() -> None:
    what = _collect_arg()
    now = datetime.now(site_render.KST)
    today = now.strftime("%Y-%m-%d")
    generated = now.strftime("%Y-%m-%d %H:%M KST")

    source_dir = ncfg.PREV_DIR or ncfg.SITE_DIR
    news_days = news_archive.load_days(source_dir)
    patent_weeks = patent_archive.load_weeks(source_dir)
    # 출원인 집계(총계·공개국별)는 주별 버킷과 분리해 누적한다 → 매일 조금씩 갱신 가능.
    pstats_store = patent_archive.load_stats(source_dir)
    seeded = patent_archive.seed_stats(pstats_store, patent_weeks)
    if seeded:
        print(f"  (예전 주별 버킷의 집계 {seeded}곳을 stats.json 으로 이전)")
    print(f"기존 아카이브: 뉴스 {len(news_days)}일 · 특허 {len(patent_weeks)}주 "
          f"· 집계 {len(pstats_store.get('totals', {}))}곳 ({source_dir})")

    # ── 뉴스 수집 ──
    if what in ("news", "both"):
        print(f"{'[MOCK] ' if ncfg.is_mock() else ''}뉴스 수집 → {today}")
        fresh, mock = news_source.collect(now)
        _, added = news_archive.merge_today(news_days, today, fresh, mock)
        print(f"  뉴스 신규 {added}건 (수집 {len(fresh)}{' MOCK' if mock else ''})")

    # ── 특허 수집 (주 단위) ──
    if what in ("patents", "both"):
        wk = patent_archive.week_start(now)
        print(f"{'[MOCK] ' if patent_source.cfg.is_mock() else ''}특허 수집 → {wk} 주")
        pfresh, pmock, pstats = patent_source.collect(now)
        _, padded = patent_archive.merge_week(patent_weeks, wk, pfresh, pmock)
        n = patent_archive.merge_stats(pstats_store, pstats, today)
        print(f"  특허 신규 {padded}건 (수집 {len(pfresh)}{' MOCK' if pmock else ''})"
              f" · 집계 갱신 {n}곳")

    # ── 출원인×공개 특허청 정확 집계 (매일 일부만 회전) ──
    # 전 출원인을 한 번에 돌리면 OPS 쿼터에 걸리므로, 매일 도는 뉴스 실행에 얹어
    # 날짜 기준으로 8곳씩 갱신한다(31곳 ≈ 4일 한 바퀴). 결과는 병합만 하고 덮어쓰지 않는다.
    if what in ("news", "offices", "both"):
        n = patent_archive.merge_stats(pstats_store, patent_source.collect_offices(now), today)
        if n:
            print(f"  공개국 집계 갱신 {n}곳 "
                  f"(누적 {len(pstats_store.get('offices', {}))}곳)")

    # ── 저장 + 전체 사이트 재생성 ──
    news_archive.save(ncfg.SITE_DIR, news_days, generated)
    patent_archive.save(ncfg.SITE_DIR, patent_weeks, generated)
    patent_archive.save_stats(ncfg.SITE_DIR, pstats_store)

    # 서술형 브리핑: 이전 아카이브 로드 → 현재 brief.json 병합(날짜별 누적) → 저장.
    briefs = brief_archive.load_briefs(source_dir)
    current = _load_brief()
    brief_archive.merge(briefs, current)
    brief_archive.save(ncfg.SITE_DIR, briefs)
    brief_list = brief_archive.sorted_list(briefs)
    if brief_list:
        print(f"  브리핑: {len(brief_list)}개 (최신 {brief_list[0].get('date','?')})")
    index = site_render.render_all(ncfg.SITE_DIR, news_days, patent_weeks,
                                   generated, briefs=brief_list, stats=pstats_store)

    nt = sum(len(d.get("articles", [])) for d in news_days.values())
    pt = sum(len(w.get("patents", [])) for w in patent_weeks.values())
    print(f"\n완료 → {index}")
    print(f"       뉴스 {len(news_days)}일/{nt}건 · 특허 {len(patent_weeks)}주/{pt}건")


if __name__ == "__main__":
    main()
