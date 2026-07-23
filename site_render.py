"""정적 사이트 렌더링 — 인터랙티브 단일 페이지 리더(SPA).

전체 아카이브(뉴스·특허)를 index.html 한 장에 인라인 JSON으로 담고, 브라우저에서
통합검색·다중필터·정렬·가벼운 개요(스탯 타일 + 스파크라인)를 수행한다.
백엔드 없음. GitHub Pages(HTTP)와 file:// 로컬 열람 모두 동작(데이터가 인라인이라
fetch/CORS 불필요). 라이트/다크 자동, 필터 상태는 URL 해시에 반영(공유 가능).

원자료(data/*.json, data/patents/*.json)는 build_site 가 계속 저장한다(아카이브 누적용).
이 모듈은 그 데이터를 하나로 합쳐 SPA 로 렌더한다.

주의(성장): 아카이브가 아주 커지면 index.html 인라인 데이터가 커진다. 지금 규모엔
충분하며, 필요 시 data/feed.json fetch + 월별 분할/지연로딩으로 확장하면 된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import news_config as ncfg
import patent_config as pcfg

KST = timezone(timedelta(hours=9))

SITE_TITLE = "전력 이슈 뉴스·특허 아카이브"
SITE_TAGLINE = "반도체 클러스터·AI 데이터센터·3대 메가프로젝트 시대의 전력 이슈 — 뉴스(매일)·특허(매주)"


def _news_feed(news_days: dict[str, dict]) -> dict:
    items = []
    per_day: dict[str, int] = {}
    for date in sorted(news_days):
        arts = news_days[date].get("articles", [])
        per_day[date] = len(arts)
        mock = bool(news_days[date].get("mock"))
        for a in arts:
            items.append({
                "title": a.get("title", ""), "url": a.get("url", ""),
                "source": a.get("source", ""), "published": a.get("published"),
                "summary": a.get("summary", ""), "category": a.get("category", "etc"),
                "date": date, "mock": mock,
            })
    return {
        "categories": [{"key": c["key"], "emoji": c["emoji"], "name": c["name"]}
                       for c in ncfg.CATEGORIES],
        "perDay": [{"x": d, "y": per_day[d]} for d in sorted(per_day)],
        "items": items,
    }


def _patent_feed(patent_weeks: dict[str, dict]) -> dict:
    items = []
    per_week: dict[str, int] = {}
    for wk in sorted(patent_weeks):
        pats = patent_weeks[wk].get("patents", [])
        per_week[wk] = len(pats)
        mock = bool(patent_weeks[wk].get("mock"))
        for p in pats:
            items.append({
                "title": p.get("title", ""), "url": p.get("url", ""),
                "assignee": p.get("assignee", ""), "number": p.get("number", ""),
                "pub_date": p.get("pub_date"), "summary": p.get("snippet", ""),
                "category": p.get("category", "etc"), "country": p.get("country", ""),
                "week": wk, "mock": mock,
            })
    return {
        "categories": [{"key": c["key"], "emoji": c["emoji"], "name": c["name"]}
                       for c in pcfg.CATEGORIES],
        "countries": [{"code": k, "emoji": v[0], "name": v[1]}
                      for k, v in pcfg.COUNTRY_LABEL.items()],
        "perWeek": [{"x": w, "y": per_week[w]} for w in sorted(per_week)],
        "items": items,
    }


def render_all(site_dir: Path, news_days: dict[str, dict],
               patent_weeks: dict[str, dict], generated: str) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    feed = {
        "generated": generated,
        "title": SITE_TITLE, "tagline": SITE_TAGLINE,
        "news": _news_feed(news_days),
        "patents": _patent_feed(patent_weeks),
    }
    payload = json.dumps(feed, ensure_ascii=False).replace("</", "<\\/")

    html = _PAGE.replace("__TITLE__", _esc(SITE_TITLE)) \
               .replace("__TAGLINE__", _esc(SITE_TAGLINE)) \
               .replace("__CSS__", _CSS) \
               .replace("__JS__", _JS) \
               .replace("__FEED__", payload)
    (site_dir / "index.html").write_text(html, encoding="utf-8")

    # 이전 구조(patents.html)로 들어오는 링크 호환 → 앱의 특허 탭으로 이동
    (site_dir / "patents.html").write_text(_REDIRECT, encoding="utf-8")
    return site_dir / "index.html"


def _esc(s: str) -> str:
    import html as _h
    return _h.escape(s or "", quote=True)


_REDIRECT = ('<!doctype html><meta charset="utf-8">'
             '<meta http-equiv="refresh" content="0; url=index.html#tab=patents">'
             '<link rel="canonical" href="index.html#tab=patents">'
             '<script>location.replace("index.html#tab=patents")</script>'
             '<p>특허 탭으로 이동합니다… <a href="index.html#tab=patents">여기</a></p>')


_CSS = """
:root{
  --bg:#F4F5F3; --card:#FFFFFF; --ink:#16181C; --muted:#6A6E76;
  --line:#E2E4E0; --accent:#E8A33D; --accent2:#3A6FB0; --chipbg:#FFFFFF;
  --shadow:0 1px 2px rgba(0,0,0,.05); --spark:#E8A33D;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0F1114; --card:#181B20; --ink:#E8EAED; --muted:#9AA0A8;
    --line:#262A31; --accent:#F0B65A; --accent2:#6FA0DC; --chipbg:#1E2127;
    --shadow:0 1px 2px rgba(0,0,0,.3); --spark:#F0B65A;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,"SFMono-Regular",Menlo,monospace}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 72px}
.mast{border-bottom:3px solid var(--ink);padding-bottom:14px;margin-bottom:0}
.mast h1{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:0 0 3px;display:flex;gap:9px;align-items:center}
.mast h1 .bolt{color:var(--accent)}
.mast .tag{color:var(--muted);font-size:13px;margin:0}
.tabs{display:flex;gap:6px;margin:0 0 16px;border-bottom:1px solid var(--line)}
.tabs button{padding:11px 18px;font:inherit;font-weight:700;font-size:14.5px;color:var(--muted);
  background:none;border:0;border-bottom:3px solid transparent;margin-bottom:-1px;cursor:pointer}
.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
.tabs button:hover{color:var(--ink)}
/* 개요 */
.overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) 1.6fr;gap:12px;margin:2px 0 18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:11px 13px;box-shadow:var(--shadow)}
.tile .k{font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.tile .v{font-size:22px;font-weight:800;margin-top:3px}
.tile .v small{font-size:12px;font-weight:600;color:var(--muted)}
.tile.spark{grid-column:span 1}
.sparkwrap{display:flex;flex-direction:column;justify-content:space-between}
.sparkwrap svg{width:100%;height:38px;display:block;margin-top:4px}
.sparkwrap .k b{color:var(--accent)}
/* 컨트롤 */
.controls{display:flex;flex-direction:column;gap:10px;margin:0 0 16px;
  position:sticky;top:0;z-index:5;background:var(--bg);padding-top:8px}
.searchrow{display:flex;gap:8px;align-items:center}
.search{flex:1;display:flex;align-items:center;gap:8px;background:var(--card);
  border:1px solid var(--line);border-radius:9px;padding:9px 13px}
.search input{flex:1;border:0;background:none;color:var(--ink);font:inherit;font-size:15px;outline:none}
.search .ico{color:var(--muted)}
.selects{display:flex;gap:8px;flex-wrap:wrap}
.selects select,.selects button.toggle{font:inherit;font-size:13px;color:var(--ink);
  background:var(--chipbg);border:1px solid var(--line);border-radius:8px;padding:7px 11px;cursor:pointer}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chips .f{font-size:12.5px;padding:5px 11px;border:1px solid var(--line);border-radius:999px;
  background:var(--chipbg);color:var(--ink);cursor:pointer;user-select:none;font:inherit;transition:all .12s}
.chips .f[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chips .f .n{color:var(--muted);margin-left:5px}
.chips .f[aria-pressed="true"] .n{color:var(--bg);opacity:.7}
.chips .f.co[aria-pressed="true"]{background:var(--accent2);border-color:var(--accent2);color:#fff}
.resline{display:flex;justify-content:space-between;align-items:center;color:var(--muted);
  font-size:12.5px;margin:2px 2px 12px}
.resline .reset{color:var(--accent2);cursor:pointer;background:none;border:0;font:inherit;font-size:12.5px}
/* 결과 카드 */
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:13px 15px;
  margin-bottom:9px;box-shadow:var(--shadow);transition:border-color .12s,transform .12s;position:relative}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card .t{font-size:15.5px;font-weight:650;line-height:1.4;margin:0 0 5px;text-decoration:none;display:block}
.card .t:hover{color:var(--accent2)}
.card .meta{color:var(--muted);font-size:12.5px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.card .meta .src{color:var(--ink);font-weight:600}
.card .meta .num{font-family:ui-monospace,Menlo,monospace;font-size:11.5px}
.card .sum{color:var(--muted);font-size:13px;margin-top:6px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-left:auto}
.card .newdot{position:absolute;top:14px;right:14px;width:7px;height:7px;border-radius:50%;
  background:var(--accent)}
.card.isnew{border-left:3px solid var(--accent)}
.mockflag{background:var(--accent);color:#1a1a1a;font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px}
.empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center;border:1px dashed var(--line);border-radius:9px}
.more{display:block;margin:14px auto 0;font:inherit;font-size:13px;font-weight:600;color:var(--ink);
  background:var(--card);border:1px solid var(--line);border-radius:8px;padding:9px 18px;cursor:pointer}
.foot{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:32px;line-height:1.7}
.foot a{color:var(--accent2)}
@media (max-width:820px){
  .overview{grid-template-columns:repeat(2,1fr)}
  .tile.spark{grid-column:1 / -1}
  .controls{position:static}
}
"""

_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__TAGLINE__">
<style>__CSS__</style></head>
<body>
<div class="wrap">
  <header class="mast">
    <h1><span class="bolt">⚡</span> __TITLE__</h1>
    <p class="tag">__TAGLINE__</p>
  </header>
  <nav class="tabs" role="tablist" aria-label="보기 전환">
    <button role="tab" id="tab-news" aria-selected="true" data-tab="news">📰 뉴스</button>
    <button role="tab" id="tab-patents" aria-selected="false" data-tab="patents">📄 특허</button>
  </nav>
  <section class="overview" id="overview" aria-label="개요"></section>
  <div class="controls">
    <div class="searchrow">
      <label class="search">
        <span class="ico" aria-hidden="true">🔍</span>
        <input id="q" type="search" placeholder="제목·요약·출처·출원인·공개번호 검색" aria-label="검색">
      </label>
      <div class="selects">
        <select id="sort" aria-label="정렬">
          <option value="new">최신순</option>
          <option value="old">오래된순</option>
        </select>
        <button class="toggle" id="newonly" aria-pressed="false" title="지난 방문 이후 새 항목만">✨ 새 항목</button>
      </div>
    </div>
    <div class="chips" id="countryChips" aria-label="국가 필터" hidden></div>
    <div class="chips" id="catChips" aria-label="카테고리 필터"></div>
  </div>
  <div class="resline">
    <span id="resCount" aria-live="polite"></span>
    <button class="reset" id="reset" hidden>필터 초기화</button>
  </div>
  <main id="results"></main>
  <button class="more" id="more" hidden>더 보기</button>
  <footer class="foot" id="foot"></footer>
</div>
<script id="feed" type="application/json">__FEED__</script>
<script>__JS__</script>
</body></html>"""

_JS = r"""
const FEED = JSON.parse(document.getElementById('feed').textContent);
const PAGE = 60;
const $ = s => document.querySelector(s);
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const LS_KEY = 'pnp_lastVisit';
const lastVisit = Number(localStorage.getItem(LS_KEY) || 0);

const state = { tab:'news', q:'', cats:new Set(), countries:new Set(), sort:'new', newonly:false, limit:PAGE };

function catMap(tab){ const m={}; FEED[tab].categories.forEach(c=>m[c.key]=c); return m; }
function itemTime(it){ const d = it.published || it.pub_date || it.date || it.week || ''; const t = Date.parse(d); return isNaN(t)?0:t; }
function isNew(it){ return lastVisit>0 && itemTime(it) > lastVisit; }

function filtered(){
  const f = FEED[state.tab];
  const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
  let out = f.items.filter(it=>{
    if(state.cats.size && !state.cats.has(it.category)) return false;
    if(state.tab==='patents' && state.countries.size && !state.countries.has(it.country)) return false;
    if(state.newonly && !isNew(it)) return false;
    if(terms.length){
      const hay = (it.title+' '+(it.summary||'')+' '+(it.source||'')+' '+(it.assignee||'')+' '+(it.number||'')).toLowerCase();
      if(!terms.every(t=>hay.includes(t))) return false;
    }
    return true;
  });
  out.sort((a,b)=> state.sort==='new' ? itemTime(b)-itemTime(a) : itemTime(a)-itemTime(b));
  return out;
}

function fmtDate(iso){ if(!iso) return ''; const t=Date.parse(iso); if(isNaN(t)) return esc(iso);
  const d=new Date(t); const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes()); }
function fmtDay(iso){ if(!iso) return ''; const t=Date.parse(iso); if(isNaN(t)) return esc(iso);
  const d=new Date(t); const p=n=>String(n).padStart(2,'0'); return (d.getMonth()+1)+'-'+p(d.getDate()); }

function card(it, cm){
  const c = cm[it.category] || {emoji:'',name:it.category};
  const nw = isNew(it);
  const bits = [];
  if(state.tab==='news'){
    if(it.source) bits.push('<span class="src">'+esc(it.source)+'</span>');
    if(it.published) bits.push('<span class="mono">'+fmtDate(it.published)+'</span>');
  } else {
    const co = (FEED.patents.countries.find(x=>x.code===it.country))||{emoji:'',name:it.country};
    bits.push('<span class="src">'+co.emoji+' '+esc(co.name)+'</span>');
    if(it.assignee) bits.push(esc(it.assignee));
    if(it.number) bits.push('<span class="num">'+esc(it.number)+'</span>');
    if(it.pub_date) bits.push('<span class="mono">공개 '+esc(it.pub_date)+'</span>');
  }
  const mock = it.mock ? ' <span class="mockflag">샘플</span>' : '';
  const meta = bits.join(' <span aria-hidden="true">·</span> ');
  const sum = it.summary ? '<div class="sum">'+esc(it.summary)+'</div>' : '';
  return '<article class="card'+(nw?' isnew':'')+'">'
    + (nw?'<span class="newdot" title="새 항목"></span>':'')
    + '<a class="t" href="'+esc(it.url)+'" target="_blank" rel="noopener">'+esc(it.title||'(제목 없음)')+'</a>'
    + '<div class="meta">'+meta+mock+'<span class="tag">'+(c.emoji||'')+' '+esc(c.name)+'</span></div>'
    + sum + '</article>';
}

function sparkline(series){
  if(!series.length) return '';
  const pts = series.slice(-40);
  const max = Math.max(1, ...pts.map(p=>p.y));
  const n = pts.length, gap = 2, W = 320, H = 38;
  const bw = Math.max(1, (W-(n-1)*gap)/n);
  let bars = '';
  pts.forEach((p,i)=>{
    const h = Math.max(1, Math.round(p.y/max*H));
    const x = i*(bw+gap), y = H-h;
    bars += '<rect x="'+x.toFixed(1)+'" y="'+y+'" width="'+bw.toFixed(1)+'" height="'+h
      +'" rx="1.5" fill="var(--spark)"><title>'+esc(p.x)+' · '+p.y+'건</title></rect>';
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="기간별 건수 추이">'+bars+'</svg>';
}

function renderOverview(){
  const f = FEED[state.tab];
  const total = f.items.length;
  const series = state.tab==='news' ? f.perDay : f.perWeek;
  const periods = series.length;
  const latest = series.length ? series[series.length-1] : {x:'-',y:0};
  const newCount = f.items.filter(isNew).length;
  const unit = state.tab==='news' ? '일' : '주';
  const ov = $('#overview');
  ov.innerHTML =
    tile('누적 '+(state.tab==='news'?'기사':'특허'), total.toLocaleString())
    + tile('수집 '+unit, periods)
    + tile('최근 '+unit, latest.y+'<small>건 · '+esc(latest.x)+'</small>')
    + tile(lastVisit? '새 항목' : '오늘 열람', lastVisit? newCount : '—')
    + '<div class="tile spark sparkwrap"><div class="k">'+(state.tab==='news'?'일별':'주별')+' 추이 <b>('+periods+')</b></div>'
      + sparkline(series) + '</div>';
}
function tile(k,v){ return '<div class="tile"><div class="k">'+k+'</div><div class="v mono">'+v+'</div></div>'; }

function renderChips(){
  const f = FEED[state.tab], cm = {};
  f.items.forEach(it=> cm[it.category]=(cm[it.category]||0)+1);
  $('#catChips').innerHTML = f.categories.filter(c=>cm[c.key]).map(c=>
    '<button class="f" data-cat="'+c.key+'" aria-pressed="'+state.cats.has(c.key)+'">'
    + c.emoji+' '+esc(c.name)+'<span class="n">'+(cm[c.key]||0)+'</span></button>').join('');
  const cc = $('#countryChips');
  if(state.tab==='patents'){
    cc.hidden = false;
    const cnt = {}; f.items.forEach(it=> cnt[it.country]=(cnt[it.country]||0)+1);
    cc.innerHTML = f.countries.filter(c=>cnt[c.code]).map(c=>
      '<button class="f co" data-country="'+c.code+'" aria-pressed="'+state.countries.has(c.code)+'">'
      + c.emoji+' '+esc(c.name)+'<span class="n">'+(cnt[c.code]||0)+'</span></button>').join('');
  } else { cc.hidden = true; cc.innerHTML=''; }
}

function render(){
  renderOverview();
  const cm = catMap(state.tab);
  const list = filtered();
  const shown = list.slice(0, state.limit);
  $('#results').innerHTML = shown.length
    ? shown.map(it=>card(it,cm)).join('')
    : '<div class="empty">조건에 맞는 항목이 없습니다.</div>';
  $('#more').hidden = list.length <= state.limit;
  $('#more').textContent = '더 보기 ('+(list.length-state.limit)+'개 남음)';
  const active = state.q || state.cats.size || state.countries.size || state.newonly;
  $('#resCount').innerHTML = '<b>'+list.length.toLocaleString()+'</b>건'
    + (active? ' <span style="opacity:.7">/ 전체 '+FEED[state.tab].items.length.toLocaleString()+'</span>' : '');
  $('#reset').hidden = !active;
  syncHash();
}

function syncHash(){
  const p = new URLSearchParams();
  p.set('tab', state.tab);
  if(state.q) p.set('q', state.q);
  if(state.cats.size) p.set('cat', [...state.cats].join(','));
  if(state.countries.size) p.set('co', [...state.countries].join(','));
  if(state.sort!=='new') p.set('sort', state.sort);
  if(state.newonly) p.set('new','1');
  const h = p.toString();
  history.replaceState(null,'', h? '#'+h : location.pathname);
}
function loadHash(){
  const p = new URLSearchParams(location.hash.replace(/^#/,''));
  if(p.get('tab')==='patents') state.tab='patents';
  state.q = p.get('q')||'';
  state.sort = p.get('sort')==='old'?'old':'new';
  state.newonly = p.get('new')==='1';
  if(p.get('cat')) p.get('cat').split(',').forEach(c=>state.cats.add(c));
  if(p.get('co')) p.get('co').split(',').forEach(c=>state.countries.add(c));
}

function setTab(t){
  if(state.tab===t) return;
  state.tab=t; state.cats.clear(); state.countries.clear(); state.limit=PAGE;
  document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected', b.dataset.tab===t));
  renderChips(); render();
}

function wire(){
  document.querySelectorAll('.tabs button').forEach(b=> b.onclick=()=>setTab(b.dataset.tab));
  let deb; $('#q').oninput = e=>{ clearTimeout(deb); deb=setTimeout(()=>{ state.q=e.target.value.trim(); state.limit=PAGE; render(); },140); };
  $('#sort').onchange = e=>{ state.sort=e.target.value; render(); };
  $('#newonly').onclick = e=>{ state.newonly=!state.newonly; e.currentTarget.setAttribute('aria-pressed',state.newonly); state.limit=PAGE; render(); };
  $('#catChips').onclick = e=>{ const b=e.target.closest('[data-cat]'); if(!b) return;
    const k=b.dataset.cat; state.cats.has(k)?state.cats.delete(k):state.cats.add(k);
    b.setAttribute('aria-pressed',state.cats.has(k)); state.limit=PAGE; render(); };
  $('#countryChips').onclick = e=>{ const b=e.target.closest('[data-country]'); if(!b) return;
    const k=b.dataset.country; state.countries.has(k)?state.countries.delete(k):state.countries.add(k);
    b.setAttribute('aria-pressed',state.countries.has(k)); state.limit=PAGE; render(); };
  $('#more').onclick = ()=>{ state.limit+=PAGE; render(); };
  $('#reset').onclick = ()=>{ state.q=''; state.cats.clear(); state.countries.clear(); state.newonly=false;
    state.limit=PAGE; $('#q').value=''; $('#newonly').setAttribute('aria-pressed','false');
    document.querySelectorAll('.chips .f').forEach(b=>b.setAttribute('aria-pressed','false')); render(); };
}

$('#foot').innerHTML = '뉴스: Google 뉴스 RSS · 특허: Google Patents 공개 데이터에서 전력 키워드로 자동 수집. '
  + '제목·요약·링크는 원문으로 연결됩니다. 본 사이트는 이슈 아카이브용이며 특정 투자·정책 판단을 권유하지 않습니다.'
  + '<br>최종 갱신 <b class="mono">'+esc(FEED.generated)+'</b> · 뉴스 '+FEED.news.items.length
  + '건 · 특허 '+FEED.patents.items.length+'건';

loadHash();
$('#q').value = state.q;
$('#sort').value = state.sort;
$('#newonly').setAttribute('aria-pressed', state.newonly);
document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected', b.dataset.tab===state.tab));
renderChips(); wire(); render();
localStorage.setItem(LS_KEY, Date.now());
"""
