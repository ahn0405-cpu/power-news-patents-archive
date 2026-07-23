"""정적 사이트 렌더링 (뉴스 탭 + 특허 탭).

- index.html          : 최신 날짜 전력 뉴스 (뉴스 탭, 기본 랜딩)
- d/YYYY-MM-DD.html   : 날짜별 뉴스 상세
- patents.html        : 최신 주 전력 특허 (특허 탭)
- p/YYYY-MM-DD.html   : 주별(월요일 시작) 특허 상세

모든 페이지 자기완결형(인라인 CSS/JS). 라이트/다크 자동, 카테고리 필터,
상단 뉴스/특허 탭, 좌우 아카이브 레일. file:// 및 서브경로 Pages 모두 동작.
KST 기준 시각 표기.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from pathlib import Path

import news_config as ncfg
import patent_config as pcfg

KST = timezone(timedelta(hours=9))

SITE_TITLE = "전력 이슈 뉴스·특허 아카이브"
SITE_TAGLINE = "반도체 클러스터·AI 데이터센터·3대 메가프로젝트 시대의 전력 이슈를 매일(뉴스)·매주(특허) 모읍니다"

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
.mast{border-bottom:3px solid var(--ink);padding-bottom:16px;margin-bottom:0}
.mast h1{font-size:26px;font-weight:800;letter-spacing:-.02em;margin:0 0 4px;
  display:flex;align-items:center;gap:10px}
.mast h1 .bolt{color:var(--accent)}
.mast h1 a{text-decoration:none}
.mast .tag{color:var(--muted);font-size:13.5px;margin:0}
.tabs{display:flex;gap:6px;margin:0 0 18px;border-bottom:1px solid var(--line)}
.tabs a{padding:11px 18px;text-decoration:none;font-size:14.5px;font-weight:700;
  color:var(--muted);border-bottom:3px solid transparent;margin-bottom:-1px}
.tabs a.on{color:var(--ink);border-bottom-color:var(--accent)}
.tabs a:hover{color:var(--ink)}
.meta{color:var(--muted);font-size:12.5px;margin:14px 0 0;display:flex;gap:16px;flex-wrap:wrap}
.meta b{color:var(--ink);font-weight:700}
.layout{display:grid;grid-template-columns:1fr 236px;gap:30px;align-items:start}
.daychip{display:inline-flex;align-items:center;gap:8px;background:var(--card);
  border:1px solid var(--line);border-radius:999px;padding:5px 14px;font-size:13px;
  margin:2px 0 16px;box-shadow:var(--shadow)}
.daychip b{font-weight:700}
.mockflag{background:var(--accent);color:#1a1a1a;font-size:11px;font-weight:700;
  padding:2px 8px;border-radius:999px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}
.filters .f{font-size:12.5px;padding:5px 12px;border:1px solid var(--line);
  border-radius:999px;background:var(--chipbg);cursor:pointer;user-select:none;transition:all .12s}
.filters .f[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
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
.card .s{color:var(--muted);font-size:12.5px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.card .s .src{color:var(--ink);font-weight:600}
.card .s .flag{font-weight:600}
.card .s .num{font-family:ui-monospace,Menlo,monospace;font-size:11.5px}
.card .sum{color:var(--muted);font-size:13px;margin-top:7px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.empty{color:var(--muted);font-size:14px;padding:34px 0;text-align:center;
  border:1px dashed var(--line);border-radius:8px}
.rail{position:sticky;top:20px}
.rail h3{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
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
  .rail{position:static;order:-1}.rail ol{max-height:none;display:flex;gap:8px;overflow-x:auto}
  .rail li a{white-space:nowrap}}
"""

_FILTER_JS = """
(function(){var fs=document.querySelectorAll('.filters .f'),secs=document.querySelectorAll('.catsec');
function apply(c){fs.forEach(function(b){b.setAttribute('aria-pressed',b.dataset.cat===c);});
secs.forEach(function(s){s.style.display=(c==='all'||s.dataset.cat===c)?'':'none';});}
fs.forEach(function(b){b.addEventListener('click',function(){apply(b.dataset.cat);});});})();
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


def _head(title: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(SITE_TAGLINE)}">
<style>{_CSS}</style></head><body><div class="wrap">"""


def _masthead(index_href: str) -> str:
    return (f'<header class="mast"><h1><span class="bolt">⚡</span> '
            f'<a href="{index_href}">{_esc(SITE_TITLE)}</a></h1>'
            f'<p class="tag">{_esc(SITE_TAGLINE)}</p></header>')


def _tabs(active: str, prefix: str) -> str:
    n = " on" if active == "news" else ""
    p = " on" if active == "patents" else ""
    return (f'<nav class="tabs">'
            f'<a class="{n.strip()}" href="{prefix}index.html">📰 뉴스</a>'
            f'<a class="{p.strip()}" href="{prefix}patents.html">📄 특허</a></nav>')


def _filters(items: list[dict], categories: list[dict]) -> str:
    counts: dict[str, int] = {}
    for a in items:
        counts[a.get("category", "etc")] = counts.get(a.get("category", "etc"), 0) + 1
    chips = [f'<span class="f" data-cat="all" aria-pressed="true">전체'
             f'<span class="n">{len(items)}</span></span>']
    for cat in categories:
        n = counts.get(cat["key"], 0)
        if not n:
            continue
        chips.append(f'<span class="f" data-cat="{cat["key"]}" aria-pressed="false">'
                     f'{cat["emoji"]} {_esc(cat["name"])}<span class="n">{n}</span></span>')
    return '<div class="filters">' + "".join(chips) + "</div>"


def _sections(items: list[dict], categories: list[dict], card_fn) -> str:
    by_cat: dict[str, list[dict]] = {}
    for a in items:
        by_cat.setdefault(a.get("category", "etc"), []).append(a)
    out = []
    for cat in categories:
        arts = by_cat.get(cat["key"], [])
        if not arts:
            continue
        cards = "\n".join(card_fn(a) for a in arts)
        out.append(f"""    <section class="catsec" data-cat="{cat['key']}">
      <div class="cathead"><span class="em">{cat['emoji']}</span>
        <h2>{_esc(cat['name'])}</h2><span class="cnt">{len(arts)}건</span></div>
{cards}
    </section>""")
    return "\n".join(out)


# ── 뉴스 카드/레일 ────────────────────────────────────────────────
def _news_card(art: dict) -> str:
    src = _esc(art.get("source", ""))
    pub = _fmt_pub(art.get("published"))
    bits = []
    if src:
        bits.append(f'<span class="src">{src}</span>')
    if pub:
        bits.append(f'<span class="mono">{pub}</span>')
    meta = ' <span aria-hidden="true">·</span> '.join(bits)
    summary = _esc(art.get("summary", ""))
    sm = f'<div class="sum">{summary}</div>' if summary else ""
    return (f'      <article class="card">\n'
            f'        <a class="t" href="{_esc(art.get("url",""))}" target="_blank" rel="noopener">'
            f'{_esc(art.get("title",""))}</a>\n'
            f'        <div class="s">{meta}</div>\n{sm}\n      </article>')


def _news_rail(days: dict[str, dict], current: str, prefix: str) -> str:
    lis = []
    for date in sorted(days, reverse=True):
        n = len(days[date].get("articles", []))
        cur = "cur" if date == current else ""
        href = "index.html" if (date == current and prefix == "") else f"{prefix}{ncfg.DAY_SUBDIR}/{date}.html"
        lis.append(f'<li><a class="{cur}" href="{href}"><span>{date}</span>'
                   f'<span class="c mono">{n}</span></a></li>')
    return '<aside class="rail"><h3>날짜별 뉴스</h3><ol>' + "".join(lis) + "</ol></aside>"


# ── 특허 카드/레일 ────────────────────────────────────────────────
def _patent_card(p: dict) -> str:
    flag, label = pcfg.COUNTRY_LABEL.get(p.get("country", ""), ("🏳️", ""))
    bits = [f'<span class="flag">{flag} {label}</span>']
    if p.get("assignee"):
        bits.append(f'<span class="src">{_esc(p["assignee"])}</span>')
    if p.get("number"):
        bits.append(f'<span class="num">{_esc(p["number"])}</span>')
    if p.get("pub_date"):
        bits.append(f'<span class="mono">공개 {_esc(p["pub_date"])}</span>')
    meta = ' <span aria-hidden="true">·</span> '.join(bits)
    summary = _esc(p.get("snippet", ""))
    sm = f'<div class="sum">{summary}</div>' if summary else ""
    return (f'      <article class="card">\n'
            f'        <a class="t" href="{_esc(p.get("url",""))}" target="_blank" rel="noopener">'
            f'{_esc(p.get("title","(제목 없음)"))}</a>\n'
            f'        <div class="s">{meta}</div>\n{sm}\n      </article>')


def _patent_rail(weeks: dict[str, dict], current: str, prefix: str) -> str:
    lis = []
    for wk in sorted(weeks, reverse=True):
        n = len(weeks[wk].get("patents", []))
        cur = "cur" if wk == current else ""
        href = "patents.html" if (wk == current and prefix == "") else f"{prefix}{pcfg.PATENT_WEEK_SUBDIR}/{wk}.html"
        lis.append(f'<li><a class="{cur}" href="{href}"><span>{wk} 주</span>'
                   f'<span class="c mono">{n}</span></a></li>')
    return '<aside class="rail"><h3>주별 특허</h3><ol>' + "".join(lis) + "</ol></aside>"


_NEWS_FOOT = ('출처: Google 뉴스 검색(RSS)에서 전력 관련 키워드로 매일 자동 수집합니다. '
              '제목·요약·링크는 각 언론사 원문으로 연결되며 저작권은 해당 매체에 있습니다. '
              '본 사이트는 이슈 아카이브용이며 특정 투자·정책 판단을 권유하지 않습니다.')
_PAT_FOOT = ('출처: Google Patents 공개 데이터에서 전력 관련 기술 키워드로 매주 자동 수집합니다. '
             '제목·요약·서지정보·링크는 각 특허 원문(Google Patents)으로 연결됩니다. '
             '비공식 데이터 소스라 일부 주는 수집이 누락될 수 있으며, 정확한 권리범위는 원문을 확인하세요.')


def _page(*, title, active, prefix, index_href, meta_html, daychip, items,
          categories, card_fn, rail, foot) -> str:
    body = (_sections(items, categories, card_fn) if items
            else '<div class="empty">이 기간에 수집된 항목이 없습니다.</div>')
    filters = _filters(items, categories) if items else ""
    return (_head(title) + _masthead(index_href) + _tabs(active, prefix)
            + meta_html + daychip
            + '<div class="layout"><main>\n' + filters + "\n" + body
            + '\n    </main>\n' + rail + "</div>"
            + f'<footer class="foot">{foot}</footer></div>'
            + f'<script>{_FILTER_JS}</script></body></html>')


def _meta(generated: str, a_label: str, a_val, b_label: str, b_val) -> str:
    return (f'<div class="meta"><span>{a_label} <b>{a_val}</b></span>'
            f'<span>{b_label} <b class="mono">{b_val}</b>건</span>'
            f'<span>최종 갱신 <b class="mono">{_esc(generated)}</b></span></div>')


# ── 최상위: 전체 사이트 렌더 ──────────────────────────────────────
def render_all(site_dir: Path, news_days: dict[str, dict],
               patent_weeks: dict[str, dict], generated: str) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / ncfg.DAY_SUBDIR).mkdir(exist_ok=True)
    (site_dir / pcfg.PATENT_WEEK_SUBDIR).mkdir(exist_ok=True)

    # ── 뉴스 페이지 ──
    news_total = sum(len(d.get("articles", [])) for d in news_days.values())

    def news_page(current, prefix, index_href):
        day = news_days.get(current, {"articles": []})
        arts = day.get("articles", [])
        mock = ' <span class="mockflag">샘플 데이터</span>' if day.get("mock") else ""
        daychip = f'<div class="daychip"><b>{current or "—"}</b> · 이 날짜 {len(arts)}건{mock}</div>'
        meta = _meta(generated, "수집일", f"{len(news_days)}일", "누적 기사", news_total)
        return _page(title=f"{SITE_TITLE} · 뉴스 {current}", active="news", prefix=prefix,
                     index_href=index_href, meta_html=meta, daychip=daychip, items=arts,
                     categories=ncfg.CATEGORIES, card_fn=_news_card,
                     rail=_news_rail(news_days, current, prefix), foot=_NEWS_FOOT)

    if news_days:
        latest = sorted(news_days, reverse=True)[0]
        (site_dir / "index.html").write_text(news_page(latest, "", "index.html"), encoding="utf-8")
        for date in news_days:
            (site_dir / ncfg.DAY_SUBDIR / f"{date}.html").write_text(
                news_page(date, "../", "../index.html"), encoding="utf-8")
    else:
        (site_dir / "index.html").write_text(news_page("", "", "index.html"), encoding="utf-8")

    # ── 특허 페이지 ──
    pat_total = sum(len(w.get("patents", [])) for w in patent_weeks.values())

    def pat_page(current, prefix, index_href):
        wk = patent_weeks.get(current, {"patents": []})
        pats = wk.get("patents", [])
        mock = ' <span class="mockflag">샘플 데이터</span>' if wk.get("mock") else ""
        kr = sum(1 for p in pats if p.get("country") == "KR")
        us = sum(1 for p in pats if p.get("country") == "US")
        daychip = (f'<div class="daychip"><b>{current or "—"} 주</b> · 이 주 {len(pats)}건 '
                   f'(🇰🇷 {kr} · 🇺🇸 {us}){mock}</div>')
        meta = _meta(generated, "수집 주", f"{len(patent_weeks)}주", "누적 특허", pat_total)
        return _page(title=f"{SITE_TITLE} · 특허 {current}", active="patents", prefix=prefix,
                     index_href=index_href, meta_html=meta, daychip=daychip, items=pats,
                     categories=pcfg.CATEGORIES, card_fn=_patent_card,
                     rail=_patent_rail(patent_weeks, current, prefix), foot=_PAT_FOOT)

    if patent_weeks:
        latest = sorted(patent_weeks, reverse=True)[0]
        (site_dir / "patents.html").write_text(pat_page(latest, "", "patents.html"), encoding="utf-8")
        for wk in patent_weeks:
            (site_dir / pcfg.PATENT_WEEK_SUBDIR / f"{wk}.html").write_text(
                pat_page(wk, "../", "../index.html"), encoding="utf-8")
    else:
        (site_dir / "patents.html").write_text(pat_page("", "", "index.html"), encoding="utf-8")

    return site_dir / "index.html"
