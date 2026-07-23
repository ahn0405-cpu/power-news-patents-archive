# ⚡ 전력 이슈 뉴스 아카이브

반도체 클러스터·AI 데이터센터·**3대 메가프로젝트** 시대에 가장 큰 변수인 **전력 이슈** —
그 관련 뉴스만 매일 자동 수집해 **날짜별로 누적 아카이브**하는 정적 웹사이트입니다.

- **API 키·결제 불필요** — Google 뉴스 RSS만 사용합니다.
- **완전 자동** — GitHub Actions가 매일 수집 → 빌드 → GitHub Pages(`gh-pages`)로 배포합니다.
- **의존성 없음** — 순수 파이썬 표준 라이브러리로만 동작합니다.

> 본 사이트는 이슈 **아카이브용**이며 특정 투자·정책 판단을 권유하지 않습니다.
> 제목·요약·링크는 각 언론사 원문으로 연결되며 저작권은 해당 매체에 있습니다.

## 카테고리 (전력 이슈 전반)
⚡ 전력수급·전력난 · 🔌 송·변전·전력망 · ☢️ 원전·SMR · 🌿 재생에너지 ·
🖥️ 데이터센터·AI 전력 · 🏗️ 반도체 클러스터·메가프로젝트 · 🏛️ 전기요금·정책·한전 · 🏭 전력설비·산업

## 구성
| 파일 | 역할 |
|------|------|
| `news_config.py` | 카테고리·검색 키워드·경로 설정 |
| `news_source.py` | Google 뉴스 RSS 수집·파싱 (차단/오프라인 시 MOCK 폴백) |
| `news_archive.py` | 날짜별 JSON 누적 + 전체 아카이브 대비 제목/URL 중복 제거 |
| `news_site.py` | 정적 사이트 렌더 (라이트/다크, 카테고리 필터, 날짜 아카이브 레일) |
| `news_main.py` | 수집→누적→사이트 재생성 오케스트레이션 |
| `.github/workflows/daily-power-news.yml` | 매일 08:00 KST 자동 실행 → gh-pages 배포 |

## 로컬 미리보기
```bash
python news_main.py          # site/ 에 생성 (설치할 의존성 없음)
# site/index.html 을 브라우저로 열기
# (네트워크 차단 환경이면 자동으로 샘플(MOCK) 데이터로 빌드됩니다)
```

## GitHub Pages 공개 (최초 1회 설정)
1. 이 저장소에 코드를 push 하고, **Actions 탭 → "전력 이슈 뉴스 아카이브" → Run workflow** 를
   한 번 수동 실행합니다 → `gh-pages` 브랜치가 생성됩니다.
2. **Settings → Pages → Source** 를 **Deploy from a branch → `gh-pages` / `(root)`** 로 지정합니다.
3. 잠시 후 `https://ahn0405-cpu.github.io/power-news-patents-archive/` 에서 열리며,
   이후 매일 08:00 KST 자동 갱신됩니다.

> 워크플로는 실행마다 `gh-pages`(직전 배포)에서 과거 아카이브를 복원한 뒤 오늘 뉴스만
> 더해 사이트 전체를 다시 만들어 배포합니다 → 히스토리는 항상 1커밋, 데이터는 계속 누적.

## 수집 시각 바꾸기
`.github/workflows/daily-power-news.yml` 의 `cron` 을 수정합니다. UTC 기준이므로
원하는 KST 시각에서 9시간을 뺀 값을 넣습니다(예: 08:00 KST → `0 23 * * *`).

## 키워드/카테고리 편집
`news_config.py` 의 `CATEGORIES` 에서 카테고리·이모지·검색어를 추가/수정합니다.
카테고리별 최대 건수 등은 `.env`(또는 워크플로 env)의 `NEWS_*` 로도 조절할 수 있습니다.
