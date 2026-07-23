"""정적 사이트 렌더링.

- index.html         : 최신 날짜의 전력 뉴스(카테고리별) + 전체 날짜 아카이브 목록
- d/YYYY-MM-DD.html  : 날짜별 상세 페이지

각 페이지는 자기완결형(인라인 CSS/JS) → file:// 로컬 열람과 GitHub Pages 모두 동작.
라이트/다크 테마 자동 대응, 카테고리 클라이언트 필터 포함.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from pathlib import Path

import news_config as cfg

KST = timezone(timedelta(hours=9))

_CSS = """
:root{
  --bg:#F4F5F3; --card:#FFFFFF; --ink:#16181C; --muted:#6A6E76;
  --line:#E2E4E0; --accent:#E8A33D; --accent2:#3A6FB0; --chipbg:#FFFFFF;
  --shadow:0 1px 2px rgba(0,0,0,.04);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0F1114; --card:#181B20; --ink:#E8EAED; --muted:#9AA0A8;
    --line:#262A31; --accent:#F0B65A; --accent2:#6FA0DC; --chipbg:#1E2127;
    --shadow:0 1px 2px rgba(0,0,0,.3);
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}
.mono{font-variant-numeric:tabular-nums;
  font-family:ui-monospace,"SFMono-Regular",Menlo,monospace}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 72px}
.mast{border-bottom:3px solid var(--ink);padding-bottom:16px;margin-bottom:18px}
.mast .bolt{color:var(--accent)}
.mast h1{font-size:27px;font-weight:800;letter-spacing:-.02em;margin:0 0 4px;
  display:flex;align-items:center;gap:10px}
.mast .tag{color:var(--muted);font-size:14px;margin:0}
.mast .meta{color:var(--muted);font-size:12.5px;margin-top:10px;
  display:flex;gap:16px;flex-wrap:wrap}
.mast .meta b{color:var(--ink);font-weight:700}
.layout{display:grid;grid-template-columns:1fr 236px;gap:30px;align-items:start}
.daychip{display:inline-flex;align-items:center;gap:8px;background:var(--card);
  border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:13px;
  margin-bottom:16px;box-shadow:var(--shadow)}
.daychip b{font-weight:700}
.mockflag{background:var(--accent);color:#1a1a1a;font-size:11px;font-weight:700;
  padding:2px 8px;border-radius:999px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}
.filters .f{font-size:12.5px;padding:5px 12px;border:1px solid var(--line);
  border-radius:999px;background:var(--chipbg);cursor:pointer;user-select:none;
  transition:all .12s}
.filters .f[aria-pressed="true"]{background:var(--ink);color:var(--bg);
  border-color:var(--ink)}
.filters .f .n{color:var(--muted);margin-left:5px}
.filters .f[aria-pressed="true"] .n{color:var(--bg);opacity:.7}
.catsec{margin:0 0 30px;scroll-margin-top:16px}
.cathead{display:flex;align-items:center;gap:9px;margin:0 0 12px;
  padding-bottom:7px;border-bottom:1px solid var(--line)}
.cathead .em{font-size:18px}
.cathead h2{font-size:16px;font-weight:700;margin:0}
.cathead .cnt{color:var(--muted);font-size:12.5px;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin-bottom:10px;box-shadow:var(--shadow);
  transition:border-color .12s,transform .12s}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card .t{font-size:15.5px;font-weight:650;line-height:1.4;margin:0 0 5px;
  text-decoration:none;display:block}
.card .t:hover{color:var(--accent2)}
.card .s{color:var(--muted);font-size:12.5px;display:flex;gap:8px;
  flex-wrap:wrap;align-items:center}
.card .s .src{color:var(--ink);font-weight:600}
.card .sum{color:var(--muted);font-size:13px;margin-top:7px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty{color:var(--muted);font-size:14px;padding:30px 0;text-align:center;
  border:1px dashed var(--line);border-radius:8px}
.rail{position:sticky;top:20px}
.rail h3{font-size:12px;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);margin:0 0 10px}
.rail ol{list-style:none;margin:0;padding:0;max-height:70vh;overflow:auto}
.rail li a{display:flex;justify-content:space-between;gap:10px;padding:7px 10px;
  border-radius:6px;text-decoration:none;font-size:13px;border:1px solid transparent}
.rail li a:hover{background:var(--card);border-color:var(--line)}
.rail li a.cur{background:var(--card);border-color:var(--accent);font-weight:700}
.rail li a .c{color:var(--muted);font-size:12px}
.foot{color:var(--muted);font-size:12px;border-top:1px solid var(--line);
  padding-top:16px;margin-top:36px;line-height:1.7}
.foot a{color:var(--accent2)}
@media (max-width:820px){.layout{grid-template-columns:1fr}
  .rail{position:static;order:-1}.rail ol{max-height:none;display:flex;
  gap:8px;overflow-x:auto}.rail li a{white-space:nowrap}}
"""

_FILTER_JS = """
(function(){
  var fs=document.querySelectorAll('.filters .f');
  var secs=document.querySelectorAll('.catsec');
  function apply(cat){
    fs.forEach(function(b){b.setAttribute('aria-pressed', b.dataset.cat===cat);});
    secs.forEach(function(s){
      s.style.display=(cat==='all'||s.dataset.cat===cat)?'':'none';});
  }
  fs.forEach(function(b){b.addEventListener('click',function(){apply(b.dataset.cat);});});
})();
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _fmt_pub(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def _article_card(art: dict) -> str:
    src = _esc(art.get("source", ""))
    pub = _fmt_pub(art.get("published"))
    meta_bits = []
    if src:
        meta_bits.append(f'<span class="src">{src}</span>')
    if pub:
        meta_bits.append(f'<span class="mono">{pub}</span>')
    meta = ' <span aria-hidden="true">·</span> '.join(meta_bits)
    summary = _esc(art.get("summary", ""))
    sum_html = f'<div class="sum">{summary}</div>' if summary else ""
    return f"""      <article class="card">
        <a class="t" href="{_esc(art.get('url',''))}" target="_blank" rel="noopener">{_esc(art.get('title',''))}</a>
        <div class="s">{meta}</div>
        {sum_html}
      </article>"""


def _category_sections(articles: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = {}
    for a in articles:
        by_cat.setdefault(a.get("category", "etc"), []).append(a)
    out = []
    for cat in cfg.CATEGORIES:
        arts = by_cat.get(cat["key"], [])
        if not arts:
            continue
        cards = "\n".join(_article_card(a) for a in arts)
        out.append(f"""    <section class="catsec" data-cat="{cat['key']}">
      <div class="cathead"><span class="em">{cat['emoji']}</span>
        <h2>{_esc(cat['name'])}</h2><span class="cnt">{len(arts)}건</span></div>
{cards}
    </section>""")
    return "\n".join(out)


def _filters(articles: list[dict]) -> str:
    counts: dict[str, int] = {}
    for a in articles:
        counts[a.get("category", "etc")] = counts.get(a.get("category", "etc"), 0) + 1
    chips = [f'<span class="f" data-cat="all" aria-pressed="true">전체'
             f'<span class="n">{len(articles)}</span></span>']
    for cat in cfg.CATEGORIES:
        n = counts.get(cat["key"], 0)
        if not n:
            continue
        chips.append(f'<span class="f" data-cat="{cat["key"]}" aria-pressed="false">'
                     f'{cat["emoji"]} {_esc(cat["name"])}<span class="n">{n}</span></span>')
    return '<div class="filters">' + "".join(chips) + "</div>"


def _rail(days: dict[str, dict], current: str, prefix: str) -> str:
    lis = []
    for date in sorted(days, reverse=True):
        n = len(days[date].get("articles", []))
        cur = " cur" if date == current else ""
        href = f"{prefix}{cfg.DAY_SUBDIR}/{date}.html"
        if date == current and prefix == "":
            href = "index.html"
        lis.append(f'<li><a class="{cur.strip()}" href="{href}">'
                   f'<span>{date}</span><span class="c mono">{n}</span></a></li>')
    return (f'<aside class="rail"><h3>날짜별 아카이브</h3><ol>'
            + "".join(lis) + "</ol></aside>")


def _page(days: dict[str, dict], current: str, generated: str,
          prefix: str, index_href: str) -> str:
    day = days.get(current, {"articles": []})
    articles = day.get("articles", [])
    total_articles = sum(len(d.get("articles", [])) for d in days.values())
    mock = day.get("mock", False)

    body = (_category_sections(articles) if articles
            else '<div class="empty">이 날짜에 수집된 전력 뉴스가 없습니다.</div>')
    filters = _filters(articles) if articles else ""
    mock_flag = ('<span class="mockflag">샘플 데이터</span>' if mock else "")

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(cfg.SITE_TITLE)} · {current}</title>
<meta name="description" content="{_esc(cfg.SITE_TAGLINE)}">
<style>{_CSS}</style></head><body><div class="wrap">
  <header class="mast">
    <h1><span class="bolt">⚡</span> <a href="{index_href}" style="text-decoration:none">{_esc(cfg.SITE_TITLE)}</a></h1>
    <p class="tag">{_esc(cfg.SITE_TAGLINE)}</p>
    <div class="meta">
      <span>수집일 <b>{len(days)}일</b></span>
      <span>누적 기사 <b class="mono">{total_articles}</b>건</span>
      <span>최종 갱신 <b class="mono">{_esc(generated)}</b></span>
    </div>
  </header>
  <div class="daychip"><b>{current}</b> · 이 날짜 {len(articles)}건 {mock_flag}</div>
  <div class="layout">
    <main>
{filters}
{body}
    </main>
{_rail(days, current, prefix)}
  </div>
  <footer class="foot">
    출처: Google 뉴스 검색(RSS)에서 전력 관련 키워드로 매일 자동 수집합니다.
    제목·요약·링크는 각 언론사 원문으로 연결되며, 저작권은 해당 매체에 있습니다.
    본 사이트는 이슈 아카이브용이며 특정 투자·정책 판단을 권유하지 않습니다.
    <br>기사 클릭 시 원문(새 탭)으로 이동합니다. · 자동 생성: 전력 이슈 뉴스 아카이브
  </footer>
</div>
<script>{_FILTER_JS}</script>
</body></html>"""


def render(site_dir: Path, days: dict[str, dict], generated: str) -> Path:
    """전체 사이트를 (재)생성. 최신 날짜 = index.html, 나머지 = d/날짜.html."""
    site_dir.mkdir(parents=True, exist_ok=True)
    day_dir = site_dir / cfg.DAY_SUBDIR
    day_dir.mkdir(exist_ok=True)

    if not days:
        (site_dir / "index.html").write_text(
            _page({}, "", generated, "", "index.html"), encoding="utf-8")
        return site_dir / "index.html"

    latest = sorted(days, reverse=True)[0]
    # index.html = 최신 날짜 (data/ 와 같은 층 → prefix "")
    (site_dir / "index.html").write_text(
        _page(days, latest, generated, "", "index.html"), encoding="utf-8")
    # d/날짜.html = 각 날짜 (한 단계 아래 → prefix "../", index 링크 "../index.html")
    for date in days:
        (day_dir / f"{date}.html").write_text(
            _page(days, date, generated, "../", "../index.html"), encoding="utf-8")
    return site_dir / "index.html"
