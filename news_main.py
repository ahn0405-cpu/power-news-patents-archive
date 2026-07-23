"""전력 이슈 뉴스 아카이브 — 메인 실행.

  수집(Google 뉴스 RSS) → 오늘 날짜에 신규 기사만 누적 → 정적 사이트 (재)생성.

로컬 미리보기:
    python news_main.py          # site/ 에 생성 → site/index.html 을 브라우저로 열기

GitHub Actions(누적 배포):
    NEWS_PREV_DIR=<gh-pages 체크아웃> NEWS_SITE_DIR=site NEWS_MOCK=off python news_main.py
  → 이전 아카이브를 읽어 오늘 것만 더하고 전체 사이트를 다시 만들어 gh-pages 로 배포.
"""
from __future__ import annotations

from datetime import datetime

import news_config as cfg
import news_source
import news_archive
import news_site


def main() -> None:
    now = datetime.now(news_site.KST)
    today = now.strftime("%Y-%m-%d")

    # 1) 이전 아카이브 로드 (Actions=PREV_DIR / 로컬=SITE_DIR)
    source_dir = cfg.PREV_DIR or cfg.SITE_DIR
    days = news_archive.load_days(source_dir)
    print(f"기존 아카이브: {len(days)}일 로드 ({source_dir})")

    # 2) 오늘 수집
    print(f"{'[MOCK] ' if cfg.is_mock() else ''}전력 뉴스 수집 시작 → {today}")
    fresh, mock = news_source.collect(now)
    print(f"수집 {len(fresh)}건" + (" (MOCK)" if mock else ""))

    # 3) 신규만 누적
    _, added = news_archive.merge_today(days, today, fresh, mock)
    print(f"오늘 신규 {added}건 추가 (중복 제외)")

    # 4) 저장 + 사이트 생성
    generated = now.strftime("%Y-%m-%d %H:%M KST")
    news_archive.save(cfg.SITE_DIR, days, generated)
    index = news_site.render(cfg.SITE_DIR, days, generated)

    total = sum(len(d.get("articles", [])) for d in days.values())
    print(f"\n완료 → {index}")
    print(f"       누적 {len(days)}일 · 총 {total}건  (브라우저로 index.html 열기)")


if __name__ == "__main__":
    main()
