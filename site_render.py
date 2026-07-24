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
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import news_config as ncfg
import patent_config as pcfg
import insights as _insights

KST = timezone(timedelta(hours=9))

# ── 출원인(assignee) 정규화 ───────────────────────────────────────
# 목적: "삼성전자"/"삼성전자주식회사"/"Samsung Electronics Co., Ltd." 를 한 항목으로.
# 접미사(주식회사·Co.,Ltd. 등)를 떼고, 한/영 이름은 별칭 표로 대표명에 병합한다.
_SUFFIX_KEYS = ["주식회사", "유한회사", "coltd", "co", "ltd", "limited", "inc",
                "corp", "corporation", "llc", "gmbh", "company", "plc", "sa",
                "nv", "ag", "holdings", "kk", "ep", "lp"]

# (대표명, 국적 ISO2, [별칭 키...])
_ALIAS_RAW = [
    ("삼성전자", "KR", ["삼성전자", "samsungelectronics", "samsungelec"]),
    ("삼성SDI", "KR", ["삼성sdi", "samsungsdi"]),
    ("삼성전기", "KR", ["삼성전기", "samsungelectromechanics"]),
    ("삼성디스플레이", "KR", ["삼성디스플레이", "samsungdisplay"]),
    ("SK하이닉스", "KR", ["sk하이닉스", "skhynix"]),
    ("SK온", "KR", ["sk온", "skon"]),
    ("LG에너지솔루션", "KR", ["lg에너지솔루션", "lgenergysolution"]),
    ("LG전자", "KR", ["lg전자", "lgelectronics"]),
    ("LG화학", "KR", ["lg화학", "lgchem"]),
    ("LG디스플레이", "KR", ["lg디스플레이", "lgdisplay"]),
    ("현대자동차", "KR", ["현대자동차", "hyundaimotor", "hyundaimotorcompany"]),
    ("기아", "KR", ["기아", "기아자동차", "kia", "kiamotors"]),
    ("현대모비스", "KR", ["현대모비스", "hyundaimobis"]),
    ("현대일렉트릭", "KR", ["현대일렉트릭", "hyundaielectric"]),
    ("한국전력공사", "KR", ["한국전력공사", "한국전력", "kepco", "koreaelectricpower"]),
    ("한국수력원자력", "KR", ["한국수력원자력", "khnp", "koreahydronuclearpower"]),
    ("한국전기연구원", "KR", ["한국전기연구원", "keri"]),
    ("한국에너지기술연구원", "KR", ["한국에너지기술연구원", "kier"]),
    ("한국전자통신연구원", "KR", ["한국전자통신연구원", "etri"]),
    ("LS일렉트릭", "KR", ["ls일렉트릭", "lselectric"]),
    ("LS전선", "KR", ["ls전선", "lscable", "lscns"]),
    ("효성중공업", "KR", ["효성중공업", "hyosungheavyindustries"]),
    ("두산에너빌리티", "KR", ["두산에너빌리티", "doosanenerbility", "두산중공업", "doosanheavyindustries"]),
    ("포스코", "KR", ["포스코", "posco", "포스코홀딩스"]),
    ("한화솔루션", "KR", ["한화솔루션", "hanwhasolutions"]),
    ("Qualcomm", "US", ["qualcomm"]),
    ("Intel", "US", ["intel"]),
    ("Micron", "US", ["micron", "microntechnology"]),
    ("Applied Materials", "US", ["appliedmaterials"]),
    ("General Electric", "US", ["generalelectric"]),
    ("Tesla", "US", ["tesla"]),
    ("Google", "US", ["google"]),
    ("Apple", "US", ["apple"]),
    ("Westinghouse", "US", ["westinghouse", "westinghouseelectric"]),
    ("TSMC", "TW", ["tsmc", "taiwansemiconductormanufacturing"]),
    ("Siemens", "DE", ["siemens"]),
    ("Bosch", "DE", ["bosch", "robertbosch"]),
    ("Panasonic", "JP", ["panasonic"]),
    ("Toyota", "JP", ["toyota", "toyotamotor"]),
    ("Sony", "JP", ["sony"]),
    ("CATL", "CN", ["catl", "contemporaryamperextechnology"]),
    ("BYD", "CN", ["byd"]),
]
_ALIASES = {a: canon for canon, _co, al in _ALIAS_RAW for a in al}
_CANON_CO = {canon: co for canon, co, _al in _ALIAS_RAW}
_HANGUL = re.compile(r"[가-힣]")


def _assignee_country(canon: str, original: str) -> str:
    """출원인 국적 추정(ISO2). 큐레이션 매핑 우선, 없으면 한글 포함 시 KR, 그 외 미상('')."""
    if canon in _CANON_CO:
        return _CANON_CO[canon]
    if _HANGUL.search(original or ""):
        return "KR"
    return ""


def _akey(s: str) -> str:
    return re.sub(r"[\s.,()·\-_/]+", "", (s or "").lower())


def _canon_assignee(name: str) -> str:
    s = (name or "").strip().strip(",.")
    if not s:
        return "(출원인 미상)"
    key = _akey(s)
    changed = True
    while changed:                       # 끝의 법인 접미사 반복 제거
        changed = False
        for suf in _SUFFIX_KEYS:
            if len(key) > len(suf) + 1 and key.endswith(suf):
                key = key[:-len(suf)]
                changed = True
    if key in _ALIASES:
        return _ALIASES[key]
    # 별칭에 없으면 원문에서 꼬리 접미사만 떼어 표시
    disp = re.sub(r"[,\s]*(주식회사|유한회사|\(주\)|㈜|Co\.?\s*,?\s*Ltd\.?|Co\.?|Ltd\.?|"
                  r"Inc\.?|Corp\.?(oration)?|L\.?L\.?C\.?|GmbH|Company|PLC|Holdings|"
                  r"S\.?A\.?|N\.?V\.?|A\.?G\.?)\s*$", "", s, flags=re.I).strip().strip(",.")
    return disp or s

SITE_TITLE = "IP·Power"
SITE_TAGLINE = "전력 이슈 뉴스(매일)·특허(매주)·트렌드 브리핑을 한자리에 — 반도체 클러스터·AI 데이터센터·3대 메가프로젝트 시대"


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
            # 출원인은 수집 시 명시(우리가 조회한 주체) → 그 값을 우선 사용.
            # 옛 데이터(applicant 없음)는 이름 정규화로 대체(하위호환).
            ap = p.get("applicant") or ""
            aname = ap or _canon_assignee(p.get("assignee", ""))
            region = p.get("country", "") if ap else \
                _assignee_country(_canon_assignee(p.get("assignee", "")), p.get("assignee", ""))
            flag = p.get("flag") or (pcfg.REGION_LABEL.get(region, ("", ""))[0])
            items.append({
                "title": p.get("title", ""), "url": p.get("url", ""),
                "assignee": p.get("assignee", ""), "number": p.get("number", ""),
                "aName": aname, "aCountry": region, "aFlag": flag, "applicant": ap,
                "office": p.get("office", ""),
                "pub_date": p.get("pub_date"), "summary": p.get("snippet", ""),
                "category": p.get("category", "etc"), "country": region,
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
               patent_weeks: dict[str, dict], generated: str,
               briefs: list[dict] | None = None) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    briefs = briefs or []
    feed = {
        "generated": generated,
        "title": SITE_TITLE, "tagline": SITE_TAGLINE,
        "brief": briefs[0] if briefs else None,   # 최신(홈 상단)
        "briefs": briefs,                          # 최신순 전체(타임라인)
        "insights": _insights.build(news_days, patent_weeks),
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
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:inherit}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,"SFMono-Regular",Menlo,monospace}
.wrap{max-width:1600px;margin:0 auto;padding:22px 32px 72px}
/* 상세(뉴스·특허) 목록은 가독성을 위해 읽기 폭을 가운데 정렬로 제한. 홈/통계는 전체 폭. */
.readcol{max-width:1120px;margin-left:auto;margin-right:auto}
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
/* 서술형 브리핑 */
.brief{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
  border-radius:11px;padding:16px 18px 14px;box-shadow:var(--shadow);margin:2px 0 14px}
.brief .bhead{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin:0 0 3px}
.brief .btag{font-size:10.5px;font-weight:800;letter-spacing:.03em;color:var(--accent);
  border:1px solid var(--accent);border-radius:999px;padding:2px 8px;white-space:nowrap}
.brief .bdate{color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.brief .bstale{color:#b06a1d;font-size:11px;font-weight:700}
@media (prefers-color-scheme:dark){ .brief .bstale{color:var(--accent)} }
.brief h2{font-size:17px;font-weight:800;letter-spacing:-.01em;line-height:1.4;margin:2px 0 9px}
.brief .bbody{font-size:13.5px;line-height:1.75;color:var(--ink);margin:0}
.brief .bbody p{margin:0 0 7px}
.brief .bbody p:last-child{margin-bottom:0}
.brief .bpoints{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:12px 0 2px}
.brief .pt{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.brief .pt .pl{font-size:12px;font-weight:800;display:flex;align-items:center;gap:5px;margin-bottom:3px}
.brief .pt .px{font-size:11.5px;color:var(--muted);line-height:1.5}
.brief .bfoot{color:var(--muted);font-size:11px;margin-top:11px;padding-top:9px;border-top:1px solid var(--line);
  display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.brief .bfoot .sep{opacity:.5}
.brief .btoggle{margin-left:auto;background:none;border:0;color:var(--accent2);font:inherit;font-size:11px;cursor:pointer}
.brief.collapsed .bbody,.brief.collapsed .bpoints{display:none}
@media (max-width:820px){ .brief .bpoints{grid-template-columns:1fr} }
/* 홈(대시보드) */
.homemode .controls,.homemode .resline,.homemode #overview,.homemode #results,
.homemode #more,.homemode #viewToggle{display:none!important}
.home{display:flex;flex-direction:column;gap:16px}
.home .sec{font-size:12px;font-weight:800;margin:4px 2px 2px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.homekpi{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.homebot{display:grid;grid-template-columns:1.45fr 1fr;gap:14px;align-items:start}
.homepanel{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:15px 16px;box-shadow:var(--shadow);min-width:0}
.homepanel h3{font-size:14px;font-weight:700;margin:0 0 3px;display:flex;align-items:center;gap:7px}
.homepanel .sub{color:var(--muted);font-size:12px;margin:0 0 12px}
.homepanel .morelink{margin-left:auto;font-size:11.5px;font-weight:600;color:var(--accent2);cursor:pointer}
.timeline{display:flex;flex-direction:column}
.timeline .tl{border-left:2px solid var(--line);padding:0 0 14px 15px;position:relative}
.timeline .tl:last-child{padding-bottom:2px}
.timeline .tl::before{content:"";position:absolute;left:-5px;top:4px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.timeline .tld{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.timeline .tlh{font-size:13px;font-weight:700;margin:2px 0 3px;cursor:pointer;line-height:1.4}
.timeline .tlh:hover{color:var(--accent2)}
.timeline .tlb{font-size:12px;color:var(--muted);line-height:1.65;display:none}
.timeline .tl.open .tlb{display:block}
.mxmini{overflow-x:auto}
@media (max-width:1100px){ .homebot{grid-template-columns:1fr} }
@media (max-width:720px){ .homekpi{grid-template-columns:repeat(2,1fr)} }
/* 트렌드 인사이트 바 */
.insights{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:12px;margin:2px 0 16px}
.insights .ipanel{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;box-shadow:var(--shadow);min-width:0}
.insights h3{font-size:12px;font-weight:800;letter-spacing:.01em;margin:0 0 2px;display:flex;
  align-items:center;gap:6px}
.insights .isub{color:var(--muted);font-size:11px;margin:0 0 10px}
.kwrap{display:flex;flex-wrap:wrap;gap:6px}
.kw{font:inherit;font-size:12.5px;border:1px solid var(--line);background:var(--chipbg);color:var(--ink);
  border-radius:999px;padding:4px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:5px;transition:all .12s}
.kw:hover{border-color:var(--accent);transform:translateY(-1px)}
.kw .c{color:var(--muted);font-variant-numeric:tabular-nums;font-size:11px}
.kw .up{color:var(--accent);font-weight:800;font-size:10.5px}
.kw.hot{border-color:var(--accent)}
.trend{display:flex;flex-direction:column;gap:7px}
.trend .row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px;font-size:12.5px;
  cursor:pointer;border-radius:6px;padding:2px 4px;margin:0 -4px}
.trend .row:hover{background:var(--bg)}
.trend .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trend .d{font-variant-numeric:tabular-nums;font-weight:700;font-size:12px;white-space:nowrap}
.trend .d .n{color:var(--muted);font-weight:600}
.trend .up{color:var(--accent)}.trend .dn{color:var(--muted)}.trend .fl{color:var(--muted)}
.ppick{display:flex;flex-direction:column;gap:2px}
.ppick .pk{display:flex;gap:8px;align-items:flex-start;text-decoration:none;color:var(--ink);
  padding:6px 4px;border-radius:6px;margin:0 -4px;border-bottom:1px solid var(--line)}
.ppick .pk:last-child{border-bottom:0}
.ppick .pk:hover{background:var(--bg)}
.ppick .pf{font-size:13px;line-height:1.5;flex:none}
.ppick .pt2{font-size:12.5px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden}
.ppick .pk:hover .pt2{color:var(--accent2)}
.ppick .who{color:var(--muted);font-size:11px;margin-left:6px;white-space:nowrap}
.iempty{color:var(--muted);font-size:12px}
@media (max-width:820px){ .insights{grid-template-columns:1fr} }
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
.selects .toggle[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
/* 저장 별표 / 읽음 / 검색어 하이라이트 */
.card{padding-right:38px}
.card .star{position:absolute;top:10px;right:10px;background:none;border:0;cursor:pointer;
  font-size:17px;line-height:1;color:var(--muted);padding:2px;transition:color .12s}
.card .star:hover{color:var(--accent)}
.card .star.on{color:var(--accent)}
.card.isread{opacity:.5}
.card.isread .t{color:var(--muted)}
.card mark{background:rgba(232,163,61,.38);color:inherit;border-radius:2px;padding:0 1px}
/* 날짜 그룹 헤더 */
.dgroup{font-size:13.5px;font-weight:800;color:var(--ink);margin:18px 2px 9px;display:flex;
  align-items:baseline;gap:8px;border-bottom:1px solid var(--line);padding-bottom:5px}
.dgroup:first-child{margin-top:2px}
.dgroup .d{font-weight:500;color:var(--muted);font-size:11.5px;font-variant-numeric:tabular-nums}
.dgroup .n{margin-left:auto;font-weight:600;color:var(--muted);font-size:11.5px}
.sparkwrap svg rect{cursor:pointer}
.sparkwrap svg rect.sel{fill:var(--accent2)}
/* 맨 위로 버튼 */
.totop{position:fixed;right:18px;bottom:18px;z-index:20;width:44px;height:44px;border-radius:50%;
  border:1px solid var(--line);background:var(--card);color:var(--ink);font-size:19px;cursor:pointer;
  box-shadow:0 3px 10px rgba(0,0,0,.18)}
.totop:hover{border-color:var(--accent)}
.foot{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:32px;line-height:1.7}
.foot a{color:var(--accent2)}
/* 목록/통계 토글 */
.viewseg{display:none;gap:0;margin:0 0 14px;border:1px solid var(--line);border-radius:9px;
  overflow:hidden;width:max-content}
.viewseg.on{display:inline-flex}
.viewseg button{font:inherit;font-size:13px;font-weight:600;color:var(--muted);background:var(--card);
  border:0;padding:8px 16px;cursor:pointer}
.viewseg button[aria-pressed="true"]{background:var(--ink);color:var(--bg)}
/* 통계 뷰 */
.stats{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.stats .panel{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:16px 17px;box-shadow:var(--shadow)}
.stats .panel.wide{grid-column:1 / -1}
.stats h3{font-size:14px;font-weight:700;margin:0 0 4px;display:flex;align-items:center;gap:7px}
.stats .sub{color:var(--muted);font-size:12px;margin:0 0 13px}
.lead{display:flex;flex-direction:column;gap:9px}
.lead .row{display:grid;grid-template-columns:170px 1fr auto;align-items:center;gap:10px}
.lead .nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lead .nm .rk{color:var(--muted);font-weight:700;margin-right:6px;font-variant-numeric:tabular-nums}
.lead .bar{height:15px;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}
.lead .val{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.lead .val .co{color:var(--muted);font-weight:500;font-size:11.5px;margin-left:5px}
.dist{display:flex;flex-direction:column;gap:12px}
.dist .row{display:grid;grid-template-columns:88px 1fr auto;align-items:center;gap:10px;font-size:13px}
.dist .bar{height:16px;background:var(--accent2);border-radius:0 4px 4px 0;min-width:2px}
.dist .val{font-weight:700;font-variant-numeric:tabular-nums}
.pmxwrap{overflow-x:auto}
.pmx{border-collapse:separate;border-spacing:2px;font-size:12.5px;min-width:100%}
.pmx th{font-weight:600;color:var(--muted);text-align:center;padding:3px 4px;font-size:13px;white-space:nowrap}
.pmx th.cnr{text-align:left;font-size:11px;font-weight:600}
.pmx td.lab{text-align:left;white-space:nowrap;font-weight:600;padding-right:8px;font-size:12px}
.pmx td.c{text-align:center;border-radius:5px;font-variant-numeric:tabular-nums;min-width:34px;
  padding:5px 4px;color:var(--muted);background:var(--bg)}
.pmx td.c.has{color:var(--ink);cursor:pointer}
.pmx td.c.has:hover{outline:2px solid var(--accent)}
.pmx td.c.tot{font-weight:800;color:var(--ink);background:transparent}
.rgsec{margin-bottom:14px}
.rgsec:last-child{margin-bottom:0}
.rghead{font-size:12.5px;font-weight:700;margin:0 0 5px;padding-bottom:3px;border-bottom:1px solid var(--line);
  display:flex;align-items:baseline;gap:6px}
.rghead .rgn{margin-left:auto;font-size:11px;font-weight:600;color:var(--muted)}
.catlead{display:flex;flex-direction:column;gap:11px}
.catlead .crow{display:flex;flex-direction:column;gap:5px}
.catlead .clab{font-size:12.5px;font-weight:700}
.catlead .ctops{display:flex;flex-wrap:wrap;gap:5px}
.catlead .cta{font-size:11.5px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;
  display:inline-flex;align-items:center;gap:5px;background:var(--bg)}
.catlead .cta .ctn{color:var(--accent2);font-weight:700;font-variant-numeric:tabular-nums;font-size:10.5px}
.statkpi{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:4px}
.statkpi .k{color:var(--muted);font-size:11.5px}
.statkpi .v{font-size:19px;font-weight:800}
.natwrap{display:grid;grid-template-columns:1.35fr 1fr;gap:18px;align-items:start}
.tilemap{display:grid;grid-template-columns:repeat(11,1fr);gap:3px}
.tilemap .cell{aspect-ratio:1/.85;border-radius:5px;border:1px solid var(--line);background:var(--bg);
  display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:15px;line-height:1}
.tilemap .cell .cv{font-size:10px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:1px}
.natbars{display:flex;flex-direction:column;gap:8px}
.natbars .row{display:grid;grid-template-columns:92px 1fr auto;align-items:center;gap:9px;font-size:13px}
.natbars .bar{height:14px;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}
.natbars .val{font-weight:700;font-variant-numeric:tabular-nums}
.unknown{color:var(--muted);font-size:12px;margin-top:8px}
@media (max-width:820px){
.stats{grid-template-columns:1fr}
.natwrap{grid-template-columns:1fr}
.lead .row{grid-template-columns:120px 1fr auto}
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
    <button role="tab" id="tab-home" aria-selected="true" data-tab="home">🏠 홈</button>
    <button role="tab" id="tab-news" aria-selected="false" data-tab="news">📰 뉴스</button>
    <button role="tab" id="tab-patents" aria-selected="false" data-tab="patents">📄 특허</button>
  </nav>
  <section class="home" id="home" aria-label="대시보드" hidden></section>
  <div class="viewseg" id="viewToggle" role="group" aria-label="특허 보기 방식">
    <button data-view="list" aria-pressed="true">목록</button>
    <button data-view="stats" aria-pressed="false">📊 통계</button>
  </div>
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
        <select id="source" aria-label="출처 필터" hidden></select>
        <button class="toggle" id="newonly" aria-pressed="false" title="지난 방문 이후 새 항목만">✨ 새 항목</button>
        <button class="toggle" id="savedonly" aria-pressed="false" title="저장한 기사만">⭐ 저장</button>
        <button class="toggle" id="unreadonly" aria-pressed="false" title="안 읽은 기사만">👁 안읽음</button>
      </div>
    </div>
    <div class="chips" id="periodBar" aria-label="기간 필터" hidden></div>
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
<button id="toTop" class="totop" aria-label="맨 위로" title="맨 위로 (스크롤)" hidden>↑</button>
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

// 타일 그리드 세계지도용 국가 배치(대략 지리적 위치, 11열×5행). 지리 데이터 불필요.
const CGRID = {
  CA:{f:'🇨🇦',n:'캐나다',c:2,r:1}, US:{f:'🇺🇸',n:'미국',c:2,r:2}, MX:{f:'🇲🇽',n:'멕시코',c:2,r:3}, BR:{f:'🇧🇷',n:'브라질',c:3,r:4},
  GB:{f:'🇬🇧',n:'영국',c:5,r:1}, SE:{f:'🇸🇪',n:'스웨덴',c:6,r:1}, FI:{f:'🇫🇮',n:'핀란드',c:7,r:1},
  NL:{f:'🇳🇱',n:'네덜란드',c:5,r:2}, DE:{f:'🇩🇪',n:'독일',c:6,r:2},
  FR:{f:'🇫🇷',n:'프랑스',c:5,r:3}, CH:{f:'🇨🇭',n:'스위스',c:6,r:3},
  ES:{f:'🇪🇸',n:'스페인',c:5,r:4}, IT:{f:'🇮🇹',n:'이탈리아',c:6,r:4}, IL:{f:'🇮🇱',n:'이스라엘',c:7,r:4},
  RU:{f:'🇷🇺',n:'러시아',c:9,r:1}, CN:{f:'🇨🇳',n:'중국',c:9,r:2}, KR:{f:'🇰🇷',n:'한국',c:10,r:2}, JP:{f:'🇯🇵',n:'일본',c:11,r:2},
  IN:{f:'🇮🇳',n:'인도',c:8,r:3}, TW:{f:'🇹🇼',n:'대만',c:10,r:3}, SG:{f:'🇸🇬',n:'싱가포르',c:9,r:4}, AU:{f:'🇦🇺',n:'호주',c:10,r:5}
};

const LS_SAVE='pnp_saved', LS_READ='pnp_read';
let saved = new Set(JSON.parse(localStorage.getItem(LS_SAVE)||'[]'));
let read  = new Set(JSON.parse(localStorage.getItem(LS_READ)||'[]'));
let briefCollapsed = localStorage.getItem('pnp_briefClosed')==='1';
function persist(){ localStorage.setItem(LS_SAVE,JSON.stringify([...saved])); localStorage.setItem(LS_READ,JSON.stringify([...read])); }

const state = { tab:'home', view:'list', q:'', cats:new Set(), countries:new Set(),
  sort:'new', newonly:false, period:'all', source:'', savedOnly:false, unreadOnly:false, limit:PAGE };

function catMap(tab){ const m={}; FEED[tab].categories.forEach(c=>m[c.key]=c); return m; }
function itemTime(it){ const d = it.published || it.pub_date || it.date || it.week || ''; const t = Date.parse(d); return isNaN(t)?0:t; }
function isNew(it){ return lastVisit>0 && itemTime(it) > lastVisit; }

function latestNewsDate(){ const p=FEED.news.perDay; return p.length? p[p.length-1].x : ''; }
function shiftDate(d, delta){ const t=new Date(d+'T00:00:00'); if(isNaN(t)) return d;
  t.setDate(t.getDate()+delta); const p=n=>String(n).padStart(2,'0');
  return t.getFullYear()+'-'+p(t.getMonth()+1)+'-'+p(t.getDate()); }
function dayLabel(d){ const L=latestNewsDate(); if(d===L) return '오늘'; if(d===shiftDate(L,-1)) return '어제'; return d; }
function weekday(d){ const t=new Date(d+'T00:00:00'); return isNaN(t)?'':'일월화수목금토'[t.getDay()]; }
function inPeriod(it){
  if(state.period==='all') return true;
  if(state.tab==='news'){
    const L=latestNewsDate();
    if(state.period==='today') return it.date===L;
    if(state.period==='7d')  return it.date >= shiftDate(L,-6);
    if(state.period==='30d') return it.date >= shiftDate(L,-29);
    return it.date===state.period;          // 특정일(스파크라인 클릭)
  }
  return it.week===state.period;            // 특허: 특정 주(스파크라인 클릭)
}
function hl(text){
  const e=esc(text);
  const terms=state.q.toLowerCase().split(/\s+/).filter(Boolean)
    .map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  if(!terms.length) return e;
  try{ return e.replace(new RegExp('('+terms.join('|')+')','gi'),'<mark>$1</mark>'); }catch(_){ return e; }
}

function filtered(){
  const f = FEED[state.tab];
  const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
  let out = f.items.filter(it=>{
    if(state.cats.size && !state.cats.has(it.category)) return false;
    if(state.tab==='patents' && state.countries.size && !state.countries.has(it.country)) return false;
    if(!inPeriod(it)) return false;
    if(state.tab==='news' && state.source && it.source!==state.source) return false;
    if(state.savedOnly && !saved.has(it.url)) return false;
    if(state.unreadOnly && read.has(it.url)) return false;
    if(state.newonly && !isNew(it)) return false;
    if(terms.length){
      const hay = (it.title+' '+(it.summary||'')+' '+(it.source||'')+' '+(it.assignee||'')+' '+(it.aName||'')+' '+(it.number||'')).toLowerCase();
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
  const sum = it.summary ? '<div class="sum">'+hl(it.summary)+'</div>' : '';
  const isS = saved.has(it.url), isR = read.has(it.url);
  return '<article class="card'+(nw?' isnew':'')+(isR?' isread':'')+'">'
    + '<button class="star'+(isS?' on':'')+'" data-save="'+esc(it.url)+'" aria-label="저장" title="저장">'+(isS?'★':'☆')+'</button>'
    + '<a class="t" href="'+esc(it.url)+'" target="_blank" rel="noopener" data-read="'+esc(it.url)+'">'+hl(it.title||'(제목 없음)')+'</a>'
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
    const sel = state.period===p.x ? ' class="sel"' : '';
    bars += '<rect'+sel+' data-x="'+esc(p.x)+'" x="'+x.toFixed(1)+'" y="'+y+'" width="'+bw.toFixed(1)+'" height="'+h
      +'" rx="1.5" fill="var(--spark)"><title>'+esc(p.x)+' · '+p.y+'건 (클릭해 필터)</title></rect>';
  });
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="기간별 건수 추이(막대 클릭 시 그 기간만)">'+bars+'</svg>';
}

function briefHTML(){
  const b = FEED.brief;   // 최신 브리핑(홈 상단)
  if(!b || !(b.headline || (b.body&&b.body.length))) return '';
  // 최신 뉴스일과 브리핑 기준일 차이 → 오래된 브리핑이면 정직하게 표시.
  let stale='';
  const L=latestNewsDate();
  if(b.date && L){ const dd=Math.round((Date.parse(L)-Date.parse(b.date))/86400000);
    if(dd>=2) stale='<span class="bstale">· '+dd+'일 전 작성</span>'; }
  const body=(b.body||[]).map(p=>'<p>'+esc(p)+'</p>').join('');
  const pts=(b.points||[]).map(p=>'<div class="pt"><div class="pl">'+esc(p.emoji||'')+' '+esc(p.label||'')
    +'</div><div class="px">'+esc(p.text||'')+'</div></div>').join('');
  const foot=[];
  if(b.author) foot.push('✍️ '+esc(b.author)+(b.mode?' · '+esc(b.mode):''));
  if(b.basis) foot.push('<span class="sep">·</span> '+esc(b.basis));
  if(b.note) foot.push('<span class="sep">·</span> '+esc(b.note));
  return '<div class="brief'+(briefCollapsed?' collapsed':'')+'">'
    + '<div class="bhead"><span class="btag">🧭 오늘의 브리핑</span>'
    + (b.date?'<span class="bdate">'+esc(b.date)+' 기준</span>':'') + stale
    + '<button class="btoggle" id="briefToggle">'+(briefCollapsed?'펼치기 ▾':'접기 ▴')+'</button></div>'
    + (b.headline?'<h2>'+esc(b.headline)+'</h2>':'')
    + '<div class="bbody">'+body+'</div>'
    + (pts?'<div class="bpoints">'+pts+'</div>':'')
    + (foot.length?'<div class="bfoot">'+foot.join(' ')+'</div>':'');
}

function insightsHTML(){
  const ins = FEED.insights;
  if(!ins || !ins.asOf) return '';
  const w = ins.window || {recentDays:7, recentWeeks:4};

  // 1) 최근 많이 언급된 키워드 (클릭 → 검색)
  const kws = (ins.trending||[]);
  const kwHtml = kws.length ? kws.map(k=>{
    const up = k.rising ? '<span class="up" title="이전 대비 증가">▲'+(k.count-k.prev)+'</span>' : '';
    return '<button class="kw'+(k.rising?' hot':'')+'" data-kw="'+esc(k.term)+'" title="'+esc(k.term)
      +' — 검색">'+esc(k.term)+'<span class="c">'+k.count+'</span>'+up+'</button>';
  }).join('') : '<span class="iempty">데이터가 쌓이면 표시됩니다.</span>';

  // 2) 이슈 흐름 (카테고리 최근 vs 이전)
  const ct = (ins.catTrend||[]).slice(0,6);
  const ctHtml = ct.length ? ct.map(r=>{
    const d=r.delta; const cls=d>0?'up':(d<0?'dn':'fl'); const sym=d>0?'▲':(d<0?'▼':'–');
    const dd=d===0?'':(' '+sym+Math.abs(d));
    return '<div class="row" data-cat="'+esc(r.key)+'" title="'+esc(r.name)+' 필터"><div class="nm">'
      +r.emoji+' '+esc(r.name)+'</div><div class="d">'+r.recent+'<span class="n">건</span>'
      +'<span class="'+cls+'">'+dd+'</span></div></div>';
  }).join('') : '<span class="iempty">–</span>';

  // 3) 이번 주 공개 특허 (질적 노출 — 건수 아님). 최신 주 특허 중 최근 공개분 일부.
  const picks = patentPicks(5);
  const cos = FEED.patents.countries;
  const pkHtml = picks.length ? picks.map(p=>{
    const co=(cos.find(x=>x.code===p.country))||{emoji:''};
    const who = p.aName ? '<span class="who">'+esc(p.aName)+'</span>' : '';
    return '<a class="pk" href="'+esc(p.url)+'" target="_blank" rel="noopener" title="'+esc(p.title)+'">'
      +'<span class="pf">'+(co.emoji||'📄')+'</span>'
      +'<span class="pt2">'+esc(p.title||'(제목 없음)')+who+'</span></a>';
  }).join('') : '<span class="iempty">이번 주 공개 특허가 아직 없습니다.</span>';

  return '<div class="insights">'
    + '<div class="ipanel"><h3>🔥 요즘 뜨는 키워드</h3>'
    + '<p class="isub">최근 '+w.recentDays+'일 뉴스 제목 · <b>▲</b>=이전 대비 증가 · 눌러서 검색</p>'
    + '<div class="kwrap">'+kwHtml+'</div></div>'
    + '<div class="ipanel"><h3>📈 이슈 흐름</h3>'
    + '<p class="isub">카테고리별 최근 '+w.recentDays+'일 새 기사 (이전 대비) · 눌러서 필터</p>'
    + '<div class="trend">'+ctHtml+'</div></div>'
    + '<div class="ipanel"><h3>📄 이번 주 공개 특허</h3>'
    + '<p class="isub">최근 공개분 일부 · 무엇을/누가 출원했는지 (건수 아님) · 클릭 시 원문</p>'
    + '<div class="ppick">'+pkHtml+'</div></div></div>';
}

function kpiHTML(){
  const n=FEED.news, p=FEED.patents;
  const nL = n.perDay.length? n.perDay[n.perDay.length-1] : {x:'-',y:0};
  const pL = p.perWeek.length? p.perWeek[p.perWeek.length-1] : {x:'-',y:0};
  return '<div class="homekpi">'
    + tile('📰 뉴스 누적', n.items.length.toLocaleString())
    + tile('📰 최근일', nL.y+'<small>건 · '+esc(nL.x)+'</small>')
    + tile('📄 특허 누적', p.items.length.toLocaleString())
    + tile('📄 최근주', pL.y+'<small>건 · '+esc(pL.x)+'</small>')
    + '</div>';
}

function matrixMiniHTML(){
  const list=FEED.patents.items; if(!list.length) return '';
  return '<div class="homepanel"><h3>🧩 출원인 × 분야 <span class="morelink" data-go="patents-stats">특허 통계 전체 →</span></h3>'
    + '<p class="sub">발행국/지역별 주요 출원인이 어느 분야에 특허를 내는지. 칸을 누르면 특허 탭 상세.</p>'
    + '<div class="mxmini">'+regionMatrixHTML(list, {})+'</div></div>';
}

function timelineHTML(){
  const past=(FEED.briefs||[]).slice(1);   // 최신은 위에 크게 노출, 나머지를 타임라인으로
  const inner = past.length ? past.map((b,i)=>{
    const body=(b.body||[]).slice(0,2).map(p=>'<p>'+esc(p)+'</p>').join('');
    return '<div class="tl" data-ti="'+i+'"><div class="tld">'+esc(b.date||'')+'</div>'
      + '<div class="tlh">'+esc(b.headline||'(제목 없음)')+'</div>'
      + '<div class="tlb">'+body+'</div></div>';
  }).join('') : '<p class="homehint">지난 브리핑이 쌓이면 여기 타임라인으로 보여요(매주 갱신).</p>';
  return '<div class="homepanel"><h3>🗓️ 지난 브리핑</h3>'
    + '<p class="sub">제목을 누르면 요지가 펼쳐집니다.</p>'
    + '<div class="timeline">'+inner+'</div></div>';
}

function renderHome(){
  const parts=[];
  const bh=briefHTML(); if(bh) parts.push(bh);
  parts.push(kpiHTML());
  const ih=insightsHTML(); if(ih) parts.push('<div class="sec">트렌드 인사이트</div>'+ih);
  parts.push('<div class="homebot">'+(matrixMiniHTML()||'')+timelineHTML()+'</div>');
  $('#home').innerHTML = parts.join('');
}

function latestPatentWeek(){ const p=FEED.patents.perWeek; return p.length? p[p.length-1].x : ''; }
function patentPicks(n){
  const items = FEED.patents.items.slice();
  const w = latestPatentWeek();
  let pool = items.filter(it=>it.week===w);
  if(pool.length < n) pool = items;                 // 최신 주가 빈약하면 전체에서
  pool.sort((a,b)=> (Date.parse(b.pub_date||b.week)||0)-(Date.parse(a.pub_date||a.week)||0));
  return pool.slice(0, n);
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
  if(state.tab==='home'){ $('#catChips').innerHTML=''; const cc=$('#countryChips'); cc.hidden=true; cc.innerHTML=''; return; }
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

function renderPeriodBar(){
  const pb=$('#periodBar');
  if(state.tab!=='news'){ pb.hidden=true; pb.innerHTML=''; return; }
  pb.hidden=false;
  const opts=[['all','전체'],['today','오늘'],['7d','최근 7일'],['30d','최근 30일']];
  let html=opts.map(o=>'<button class="f" data-period="'+o[0]+'" aria-pressed="'+(state.period===o[0])+'">'+o[1]+'</button>').join('');
  if(state.period!=='all' && !opts.some(o=>o[0]===state.period))
    html+='<button class="f" data-period="'+esc(state.period)+'" aria-pressed="true">📅 '+esc(state.period)+' ✕</button>';
  pb.innerHTML=html;
}
function renderSource(){
  const sel=$('#source');
  if(state.tab!=='news'){ sel.hidden=true; return; }
  sel.hidden=false;
  if(!sel.dataset.built){
    const srcs=[...new Set(FEED.news.items.map(n=>n.source).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
    sel.innerHTML='<option value="">전체 출처</option>'+srcs.map(s=>'<option value="'+esc(s)+'">'+esc(s)+'</option>').join('');
    sel.dataset.built='1';
  }
  sel.value=state.source;
}

function render(){
  const home = state.tab==='home';
  document.querySelector('.wrap').classList.toggle('homemode', home);
  $('#home').hidden = !home;
  if(home){ renderHome(); updateViewToggle(); syncHash(); return; }
  renderOverview();
  renderPeriodBar(); renderSource();
  const list = filtered();
  const active = state.q || state.cats.size || state.countries.size || state.newonly
    || state.period!=='all' || state.source || state.savedOnly || state.unreadOnly;
  $('#resCount').innerHTML = '<b>'+list.length.toLocaleString()+'</b>건'
    + (active? ' <span style="opacity:.7">/ 전체 '+FEED[state.tab].items.length.toLocaleString()+'</span>' : '');
  $('#reset').hidden = !active;
  const isStats = state.tab==='patents' && state.view==='stats';
  $('#results').classList.toggle('readcol', !isStats);   // 목록=읽기폭, 통계=전체폭
  if(isStats){
    $('#results').innerHTML = renderStats(list);
    $('#more').hidden = true;
    syncHash(); return;
  }
  const cm = catMap(state.tab);
  const shown = list.slice(0, state.limit);
  if(!shown.length){
    $('#results').innerHTML = '<div class="empty">조건에 맞는 항목이 없습니다.</div>';
  } else if(state.tab==='news'){
    // 날짜별 그룹 헤더
    const byDay={}; const order=[];
    shown.forEach(it=>{ const d=it.date||'?'; if(!(d in byDay)){byDay[d]=[]; order.push(d);} byDay[d].push(it); });
    $('#results').innerHTML = order.map(d=>{
      const lbl=dayLabel(d), wd=weekday(d);
      const dateSpan = (lbl!==d? '<span class="d">'+esc(d)+'</span>':'') + (wd? '<span class="d">('+wd+')</span>':'');
      return '<div class="dgroup">'+esc(lbl)+dateSpan
        + '<span class="n">'+byDay[d].length+'건</span></div>'
        + byDay[d].map(it=>card(it,cm)).join('');
    }).join('');
  } else {
    $('#results').innerHTML = shown.map(it=>card(it,cm)).join('');
  }
  $('#more').hidden = list.length <= state.limit;
  $('#more').textContent = '더 보기 ('+(list.length-state.limit)+'개 남음)';
  syncHash();
}

// 출원인 집계(표본 내 건수 + 분야 그리드), 건수 내림차순
function _rankApplicants(list){
  const byA={};
  list.forEach(it=>{ const nm=it.aName||'(미상)';
    const o=byA[nm]||(byA[nm]={cnt:0, flag:it.aFlag||'', region:it.aCountry||'', grid:{}});
    o.cnt++; o.grid[it.category]=(o.grid[it.category]||0)+1; if(!o.flag)o.flag=it.aFlag||''; });
  return Object.keys(byA).map(nm=>Object.assign({name:nm}, byA[nm]))
    .sort((a,b)=> b.cnt-a.cnt || a.name.localeCompare(b.name));
}
// 한 지역(부분집합)의 출원인×분야 표. opts.total → 합계 열
function matrixTableHTML(ranked, opts){
  const cats=FEED.patents.categories; opts=opts||{};
  if(!ranked.length) return '';
  let maxCell=1; ranked.forEach(r=>cats.forEach(c=>{const v=r.grid[c.key]||0; if(v>maxCell)maxCell=v;}));
  const head='<tr><th class="cnr"></th>'+cats.map(c=>'<th title="'+esc(c.name)+'">'+c.emoji+'</th>').join('')
    +(opts.total?'<th>합계</th>':'')+'</tr>';
  const body=ranked.map(r=>{
    const cells=cats.map(c=>{ const v=r.grid[c.key]||0; const a=v?(0.14+v/maxCell*0.78).toFixed(2):0;
      const st=v?('background:rgba(58,111,176,'+a+');color:'+(v/maxCell>0.55?'#fff':'inherit')):'';
      const attr=v?(' class="c has" data-ap="'+esc(r.name)+'" data-cat="'+c.key+'" title="'+esc(r.name)+' · '+esc(c.name)+' '+v+'건"'):' class="c"';
      return '<td'+attr+' style="'+st+'">'+(v||'·')+'</td>'; }).join('');
    return '<tr><td class="lab">'+(r.flag||'')+' '+esc(r.name)+'</td>'+cells
      +(opts.total?'<td class="c tot">'+r.cnt+'</td>':'')+'</tr>';
  }).join('');
  return '<div class="pmxwrap"><table class="pmx"><thead>'+head+'</thead><tbody>'+body+'</tbody></table></div>';
}
// 발행국/지역별로 나눈 매트릭스(미국·한국·중국·일본·유럽 순, 있는 지역만)
function regionMatrixHTML(list, opts){
  const html=FEED.patents.countries.map(rg=>{
    const sub=list.filter(it=>it.aCountry===rg.code);
    if(!sub.length) return '';
    const ranked=_rankApplicants(sub);
    return '<div class="rgsec"><div class="rghead">'+rg.emoji+' <b>'+esc(rg.name)+'</b>'
      + ' <span class="rgn">'+sub.length+'건 · 출원인 '+ranked.length+'</span></div>'
      + matrixTableHTML(ranked, opts)+'</div>';
  }).filter(Boolean).join('');
  return html || '<p class="sub" style="margin:0">수집된 특허가 없습니다(주별 회전 수집으로 채워집니다).</p>';
}

function renderStats(list){
  if(!list.length) return '<div class="empty">조건에 맞는 특허가 없습니다.</div>';
  const cats=FEED.patents.categories, regions=FEED.patents.countries;
  const ranked=_rankApplicants(list);
  const uniq=ranked.length, topA=ranked[0];
  const regCnt={}; ranked.forEach(r=>{ regCnt[r.region]=(regCnt[r.region]||0)+1; });
  const regChips=regions.map(rg=>regCnt[rg.code]?(rg.emoji+regCnt[rg.code]):'').filter(Boolean).join(' ');
  // 분야별 주요 출원인
  const byCatA={}; list.forEach(it=>{const k=it.category,nm=it.aName||'(미상)'; (byCatA[k]||(byCatA[k]={}))[nm]=(byCatA[k][nm]||0)+1;});
  const catLeadRows=cats.filter(c=>byCatA[c.key]).map(c=>{
    const entries=Object.entries(byCatA[c.key]).filter(([n])=>n!=='(미상)').sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
    const chips=entries.slice(0,3).map(([n,v])=>'<span class="cta">'+esc(n)+(v>1?'<span class="ctn">'+v+'</span>':'')+'</span>').join('');
    return '<div class="crow"><div class="clab">'+c.emoji+' '+esc(c.name)+'</div><div class="ctops">'+(chips||'<span class="unknown">—</span>')+'</div></div>';
  }).join('');
  // 랭킹(전 지역 통합)
  const top=ranked.slice(0,15), maxA=top[0].cnt||1;
  const leadRows=top.map((r,i)=>{ const w=Math.max(2,r.cnt/maxA*100);
    return '<div class="row"><div class="nm" title="'+esc(r.name)+'"><span class="rk">'+(i+1)+'</span>'
      + (r.flag||'')+' '+esc(r.name)+'</div><div class="bar" style="width:'+w.toFixed(1)+'%"></div>'
      + '<div class="val">'+r.cnt+'</div></div>'; }).join('');

  return '<div class="stats">'
    + '<div class="panel wide"><div class="statkpi">'
      + '<div><div class="k">분석 출원인</div><div class="v mono">'+uniq
        + ' <span style="font-size:12px;color:var(--muted)">'+regChips+'</span></div></div>'
      + '<div><div class="k">수집 특허(표본)</div><div class="v mono">'+list.length.toLocaleString()+'</div></div>'
      + '<div><div class="k">최다 출원인</div><div class="v">'+(topA.flag||'')+' '+esc(topA.name)
        + ' <span style="font-size:14px;color:var(--muted)" class="mono">'+topA.cnt+'건</span></div></div>'
      + '</div></div>'
    + '<div class="panel wide"><h3>🧩 출원인 × 분야 매트릭스 <span style="color:var(--muted);font-weight:600;font-size:12px">발행국/지역별</span></h3>'
      + '<p class="sub">각 지역(미국·한국·중국·일본·유럽) 주요 출원인이 <b>어느 분야에</b> 최근 특허를 냈는지(표본 내 건수, 진할수록 많음). 칸을 누르면 해당 출원인·분야 특허를 봅니다.</p>'
      + regionMatrixHTML(list, {total:true}) + '</div>'
    + '<div class="panel"><h3>🏭 분야별 주요 출원인</h3>'
      + '<p class="sub">각 분야에서 자주 등장한 출원인(표본 내 등장 횟수).</p>'
      + '<div class="catlead">'+catLeadRows+'</div></div>'
    + '<div class="panel"><h3>🏆 출원인 랭킹 <span style="color:var(--muted);font-weight:600;font-size:12px">상위 '+top.length+' / '+uniq+'</span></h3>'
      + '<p class="sub">전 지역 통합 · 표본 내 총 출원 수.</p>'
      + '<div class="lead">'+leadRows+'</div></div>'
    + '</div>';
}

function updateViewToggle(){
  const vt = $('#viewToggle');
  vt.classList.toggle('on', state.tab==='patents');
  vt.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed', b.dataset.view===state.view));
}

function syncHash(){
  const p = new URLSearchParams();
  p.set('tab', state.tab);
  if(state.q) p.set('q', state.q);
  if(state.cats.size) p.set('cat', [...state.cats].join(','));
  if(state.countries.size) p.set('co', [...state.countries].join(','));
  if(state.sort!=='new') p.set('sort', state.sort);
  if(state.newonly) p.set('new','1');
  if(state.period!=='all') p.set('period', state.period);
  if(state.source) p.set('src', state.source);
  if(state.savedOnly) p.set('saved','1');
  if(state.unreadOnly) p.set('unread','1');
  if(state.tab==='patents' && state.view==='stats') p.set('view','stats');
  const h = p.toString();
  history.replaceState(null,'', h? '#'+h : location.pathname);
}
function loadHash(){
  const p = new URLSearchParams(location.hash.replace(/^#/,''));
  const t=p.get('tab'); if(t==='news'||t==='patents'||t==='home') state.tab=t;
  if(state.tab==='patents' && p.get('view')==='stats') state.view='stats';
  state.q = p.get('q')||'';
  state.sort = p.get('sort')==='old'?'old':'new';
  state.newonly = p.get('new')==='1';
  state.period = p.get('period')||'all';
  state.source = p.get('src')||'';
  state.savedOnly = p.get('saved')==='1';
  state.unreadOnly = p.get('unread')==='1';
  if(p.get('cat')) p.get('cat').split(',').forEach(c=>state.cats.add(c));
  if(p.get('co')) p.get('co').split(',').forEach(c=>state.countries.add(c));
}

function syncTabsUI(){ document.querySelectorAll('.tabs button')
  .forEach(b=>b.setAttribute('aria-selected', b.dataset.tab===state.tab)); }

// 홈에서 특정 탭으로 이동하며 필터를 적용(키워드→검색, 카테고리·매트릭스→필터)
function gotoTab(t, opts){ opts=opts||{};
  state.tab=t; state.view=opts.view||'list';
  state.cats.clear(); state.countries.clear(); state.period='all'; state.source='';
  state.q=opts.q||''; if(opts.cat) state.cats.add(opts.cat);
  state.limit=PAGE;
  const q=$('#q'); if(q) q.value=state.q;
  syncTabsUI(); updateViewToggle(); renderChips(); render();
  window.scrollTo({top:0, behavior:'smooth'});
}

function setTab(t){
  if(state.tab===t) return;
  state.tab=t; state.view='list'; state.cats.clear(); state.countries.clear();
  state.period='all'; state.source=''; state.limit=PAGE;
  syncTabsUI();
  updateViewToggle(); renderChips(); render();
}

function wire(){
  document.querySelectorAll('.tabs button').forEach(b=> b.onclick=()=>setTab(b.dataset.tab));
  $('#viewToggle').onclick = e=>{ const b=e.target.closest('[data-view]'); if(!b) return;
    state.view=b.dataset.view; updateViewToggle(); render(); };
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
  $('#savedonly').onclick = e=>{ state.savedOnly=!state.savedOnly; e.currentTarget.setAttribute('aria-pressed',state.savedOnly); state.limit=PAGE; render(); };
  $('#unreadonly').onclick = e=>{ state.unreadOnly=!state.unreadOnly; e.currentTarget.setAttribute('aria-pressed',state.unreadOnly); state.limit=PAGE; render(); };
  $('#source').onchange = e=>{ state.source=e.target.value; state.limit=PAGE; render(); };
  $('#periodBar').onclick = e=>{ const b=e.target.closest('[data-period]'); if(!b) return;
    const p=b.dataset.period; state.period=(p===state.period && p!=='all')?'all':p; state.limit=PAGE; render(); };
  $('#overview').onclick = e=>{ const r=e.target.closest('rect[data-x]'); if(!r) return;
    const x=r.getAttribute('data-x'); state.period=(state.period===x?'all':x); state.limit=PAGE; render(); };
  $('#home').onclick = e=>{
    // 브리핑 접기/펼치기
    if(e.target.closest('#briefToggle')){ briefCollapsed=!briefCollapsed;
      localStorage.setItem('pnp_briefClosed', briefCollapsed?'1':'0'); renderHome(); return; }
    // 지난 브리핑 타임라인 펼치기
    const tl=e.target.closest('.tl'); if(tl && !e.target.closest('[data-go]')){ tl.classList.toggle('open'); return; }
    // 키워드 → 뉴스 탭에서 검색
    const kw=e.target.closest('[data-kw]'); if(kw){ gotoTab('news', {q:kw.getAttribute('data-kw')}); return; }
    // 특허 통계 전체 보기
    const go=e.target.closest('[data-go]'); if(go){ gotoTab('patents', {view:'stats'}); return; }
    // 매트릭스 칸 → 특허 탭에서 그 출원인·분야
    const mc=e.target.closest('.pmx td.has[data-ap]');
    if(mc){ gotoTab('patents', {q:mc.getAttribute('data-ap'), cat:mc.getAttribute('data-cat')}); return; }
    // 이슈 흐름 행 → 뉴스 탭 카테고리 필터
    const row=e.target.closest('.trend [data-cat]');
    if(row){ gotoTab('news', {cat:row.getAttribute('data-cat')}); return; }
  };
  $('#results').addEventListener('click', e=>{
    // 매트릭스 칸 클릭 → 그 출원인·분야로 좁혀 목록 보기
    const mc=e.target.closest('.pmx td.has[data-ap]');
    if(mc){ state.q=mc.getAttribute('data-ap'); $('#q').value=state.q;
      state.cats=new Set([mc.getAttribute('data-cat')]); state.view='list';
      state.limit=PAGE; updateViewToggle(); renderChips(); render();
      $('#results').scrollIntoView({behavior:'smooth',block:'start'}); return; }
    const sb=e.target.closest('[data-save]');
    if(sb){ e.preventDefault(); const u=sb.getAttribute('data-save');
      saved.has(u)?saved.delete(u):saved.add(u); persist(); render(); return; }
    const a=e.target.closest('a.t[data-read]');
    if(a){ const u=a.getAttribute('data-read'); if(!read.has(u)){ read.add(u); persist();
      const cd=a.closest('.card'); if(cd) cd.classList.add('isread'); } }
  });
  $('#reset').onclick = ()=>{ state.q=''; state.cats.clear(); state.countries.clear(); state.newonly=false;
    state.period='all'; state.source=''; state.savedOnly=false; state.unreadOnly=false;
    state.limit=PAGE; $('#q').value='';
    ['newonly','savedonly','unreadonly'].forEach(id=>$('#'+id).setAttribute('aria-pressed','false'));
    document.querySelectorAll('.chips .f').forEach(b=>b.setAttribute('aria-pressed','false')); render(); };
  const toTop=$('#toTop');
  addEventListener('scroll', ()=>{ toTop.hidden = scrollY < 500; }, {passive:true});
  toTop.onclick = ()=> scrollTo({top:0, behavior:'smooth'});
  addEventListener('keydown', e=>{
    const tag=(document.activeElement&&document.activeElement.tagName)||'';
    if(e.key==='/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)){ e.preventDefault(); $('#q').focus(); }
    else if(e.key==='Escape' && document.activeElement===$('#q')){ $('#q').blur(); }
  });
}

$('#foot').innerHTML = '뉴스: Google 뉴스 RSS · 특허: Google Patents 공개 데이터에서 전력 키워드로 자동 수집. '
  + '제목·요약·링크는 원문으로 연결됩니다. 본 사이트는 이슈 아카이브용이며 특정 투자·정책 판단을 권유하지 않습니다.'
  + '<br>최종 갱신 <b class="mono">'+esc(FEED.generated)+'</b> · 뉴스 '+FEED.news.items.length
  + '건 · 특허 '+FEED.patents.items.length+'건';

loadHash();
$('#q').value = state.q;
$('#sort').value = state.sort;
$('#newonly').setAttribute('aria-pressed', state.newonly);
$('#savedonly').setAttribute('aria-pressed', state.savedOnly);
$('#unreadonly').setAttribute('aria-pressed', state.unreadOnly);
document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected', b.dataset.tab===state.tab));
updateViewToggle(); renderChips(); wire(); render();
localStorage.setItem(LS_KEY, Date.now());
"""
