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
from datetime import timezone, timedelta
from pathlib import Path

import news_config as ncfg
import patent_config as pcfg
import insights as _insights
import ip_guide

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


# 검색어가 넓으면 자회사 문서까지 딸려 온다. 실측(8/5): 'Siemens' 로 담긴 54건 중
# 36건이 Siemens Energy·Gamesa 문서였다. OPS 총계는 출원인별 독립 질의라 손쓸 수
# 없지만, **목록은 원문 출원인명이 정답을 갖고 있다** → 더 구체적인 출원인의 검색
# 어구가 원문에 들어 있으면 그쪽으로 옮긴다(어구가 길수록 구체적이다). 수집을 다시
# 하지 않아도 이미 쌓인 데이터가 이 경로에서 교정된다.
_SPECIFIC = sorted(
    ((t.lower(), a["name"], a["region"], a["flag"])
     for a in pcfg.APPLICANTS
     for t in ([a["q"]] if isinstance(a["q"], str) else a["q"])),
    key=lambda x: -len(x[0]))


def _reassign(raw: str, cur: str):
    """원문 출원인명이 더 구체적인 출원인을 가리키면 (이름, 지역, 국기). 아니면 None.

    어느 어구도 안 맞으면 손대지 않는다 — 한글 표기처럼 검색어와 형태가 다른
    경우까지 억지로 옮기면 오히려 틀린다.
    """
    r = (raw or "").lower()
    if not r:
        return None
    for t, name, region, flag in _SPECIFIC:
        if t in r:
            return None if name == cur else (name, region, flag)
    return None


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

SITE_TITLE = "IP-Power 플랫폼"
SITE_TAGLINE = "전력 이슈 뉴스와 특허를 한자리에 — 매일 자동으로 모으고 살핍니다"
SITE_ORG = "지식재산처 전기통신심사국 전기심사과"
# 헤더에서는 CI 에 '지식재산처' 가 이미 들어 있으므로 소속 부서만 덧붙인다.
SITE_DEPT = "전기통신심사국 전기심사과"
# 호스팅 주소가 기관 도메인이 아니어서, 이용자가 주소만으로는 공식 서비스인지 확인할 수
# 없다. 기관 사이트(스마트전력 연구회)에서 이 사이트로 링크를 걸고, 여기서도 그 페이지로
# 되걸어 양방향으로 확인되게 한다(같은 CI 를 붙인 사칭 사이트와 구별되는 지점).
SITE_CLUB = "스마트전력 연구회"
SITE_ORG_URL = ("https://www.moip.go.kr/club/front/main/index/"
                "mainIndex.do?clubId=display")

# 운영 기관 CI. assets/ci.svg|png|jpg 중 먼저 발견되는 파일을 빌드 시점에 data URI 로
# 인라인한다(사이트가 index.html 한 장으로 자족하는 구조라 외부 파일을 두지 않는다).
# 파일이 없으면 로고 없이 기관명만 나가므로 빌드가 깨지지 않는다.
_CI_MIME = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _ci_markup() -> str:
    import base64
    for name in ("ci.svg", "ci.png", "ci.jpg", "ci.jpeg"):
        p = Path(__file__).resolve().parent / "assets" / name
        if not p.is_file():
            continue
        src = "data:%s;base64,%s" % (_CI_MIME[p.suffix.lower()],
                                     base64.b64encode(p.read_bytes()).decode())
        return '<img class="ci" src="%s" alt="%s" width="150">' % (src, _esc(SITE_ORG))
    return ""


def _squash(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (s or "").lower())


def _echoes_title(summary: str, title: str) -> bool:
    """요약이 제목을 사실상 되풀이하는가(구글 뉴스 description 이 흔히 그렇다)."""
    st, tt = _squash(summary), _squash(title)
    return bool(tt) and (st.startswith(tt) or tt.startswith(st))


def _news_feed(news_days: dict[str, dict]) -> dict:
    items = []
    per_day: dict[str, int] = {}
    for date in sorted(news_days):
        arts = news_days[date].get("articles", [])
        per_day[date] = len(arts)
        mock = bool(news_days[date].get("mock"))
        for a in arts:
            # 페이로드 절약: RSS 요약은 제목을 그대로 되풀이하는 경우가 많다 →
            # 제목과 실질적으로 같으면 싣지 않는다(카드에서 어차피 중복 표시).
            title = a.get("title", "")
            summary = (a.get("summary") or "").strip()
            it = {
                "title": title, "url": a.get("url", ""),
                "source": a.get("source", ""), "published": a.get("published"),
                "category": a.get("category", "etc"), "date": date,
            }
            if summary and not _echoes_title(summary, title):
                it["summary"] = summary
            if mock:
                it["mock"] = True
            items.append(it)
    return {
        "categories": [{"key": c["key"], "emoji": c["emoji"], "name": c["name"]}
                       for c in ncfg.CATEGORIES],
        "perDay": [{"x": d, "y": per_day[d]} for d in sorted(per_day)],
        "items": items,
    }


TOTAL_FIX_MIN = 10      # 이보다 표본이 적으면 구성비가 튀어 총계를 손대지 않는다
TOTAL_FIX_SHARE = 0.10  # 이 비율 이상 섞였을 때만 (몇 건은 표본 오차와 구분되지 않는다)


def _patent_feed(patent_weeks: dict[str, dict], stats: dict | None = None) -> dict:
    items = []
    per_week: dict[str, int] = {}
    # 넓은 검색어가 자회사까지 쓸어 온 정도를 센다: 그 이름으로 조회해 담긴 문서 수(q_all)
    # 대비 실제로 그 회사 것이었던 수(q_own). OPS 총계는 이 비율만큼 부풀려져 있다.
    q_all: dict[str, int] = {}
    q_own: dict[str, int] = {}
    for wk in sorted(patent_weeks):
        pats = patent_weeks[wk].get("patents", [])
        per_week[wk] = len(pats)
        week_mock = bool(patent_weeks[wk].get("mock"))
        for p in pats:
            # 출원인은 수집 시 명시(우리가 조회한 주체) → 그 값을 우선 사용.
            # 옛 데이터(applicant 없음)는 이름 정규화로 대체(하위호환).
            ap = p.get("applicant") or ""
            aname = ap or _canon_assignee(p.get("assignee", ""))
            region = p.get("country", "") if ap else \
                _assignee_country(_canon_assignee(p.get("assignee", "")), p.get("assignee", ""))
            flag = p.get("flag") or (pcfg.REGION_LABEL.get(region, ("", ""))[0])
            # 페이로드 절약: 원문 assignee 는 정규화명(aName)과 다를 때만, snippet 은
            # 있을 때만 담는다. cpc 는 카드에 분류 근거로 보여주므로 상위 3개만.
            raw = p.get("assignee", "")
            fix = _reassign(raw, aname)     # 자회사 문서를 모회사 밑에 두지 않는다
            if ap:
                q_all[ap] = q_all.get(ap, 0) + 1
                if not fix:
                    q_own[ap] = q_own.get(ap, 0) + 1
            if fix:
                aname, region, flag = fix
            it = {
                "title": p.get("title", ""), "url": p.get("url", ""),
                "number": p.get("number", ""),
                "aName": aname, "aCountry": region, "aFlag": flag,
                "office": p.get("office", ""),
                "pub_date": p.get("pub_date"),
                "category": p.get("category", "etc"), "country": region,
                "week": wk,
            }
            if raw and raw != aname:
                it["assignee"] = raw
            if p.get("snippet"):
                it["summary"] = p["snippet"]
            if p.get("cpc"):
                it["cpc"] = p["cpc"][:3]
            # 항목별 mock 우선(없으면 주 단위 — 옛 데이터 하위호환)
            if p.get("mock", week_mock):
                it["mock"] = True
            items.append(it)
    # 출원인별 '실제 전체 건수'(OPS @total-result-count). 저장 목록은 상한까지의
    # 표본이지만 이 값은 상한과 무관해, 랭킹·규모 비교를 왜곡 없이 할 수 있다.
    # 지금은 stats.json(주별 버킷과 분리해 매일 누적)이 정본이고, 주별 버킷 읽기는
    # 예전 데이터 하위호환용이다 → 먼저 주별을 깔고 stats 로 덮는다.
    totals: dict[str, int] = {}
    offices: dict[str, dict] = {}
    for wk in sorted(patent_weeks):                 # 최신 주 값이 이기도록 오름차순
        for k, v in (patent_weeks[wk].get("totals") or {}).items():
            totals[k] = v
        for k, v in (patent_weeks[wk].get("offices") or {}).items():
            offices[k] = v
    totals.update((stats or {}).get("totals") or {})
    for k, v in ((stats or {}).get("offices") or {}).items():
        offices[k] = v

    # 총계 보정. 총계는 출원인별 독립 질의라 공개번호 dedup 이 듣지 않는다 →
    # 넓은 검색어는 자회사 문서까지 세어 규모를 부풀린다. 검색어를 모회사 법인명으로
    # 좁혀 봤지만 OPS 총계는 183 그대로였다(8/5 실측) — 이름으로는 갈라지지 않는다.
    # 대신 표본이 알려 준 구성비로 총계를 깎는다. Siemens 표본 54건 중 제 것은
    # 18건이었으므로 183 × 18/54 ≈ 61. (Energy 53 · Gamesa 75 를 뺀 55 와도 가깝다.)
    # 표본이 너무 작으면 비율이 튀므로 일정 수 이상일 때만 손댄다.
    adjusted: dict[str, dict] = {}
    for name, n_all in q_all.items():
        n_own = q_own.get(name, 0)
        if n_all < TOTAL_FIX_MIN or name not in totals:
            continue
        # 몇 건 섞인 정도는 손대지 않는다 — 표본 오차와 구분이 안 된다.
        if (n_all - n_own) / n_all < TOTAL_FIX_SHARE:
            continue
        raw_total = totals[name]
        totals[name] = max(n_own, round(raw_total * n_own / n_all))
        adjusted[name] = {"raw": raw_total, "kept": n_own, "of": n_all}

    # 실제 '공개일' 범위 — 아카이브에 담긴 특허가 어느 기간 공개분인지 알려준다.
    # (주별 버킷 키는 '수집한 주'라서 공개 기간과 다르다 → 혼동 방지용으로 따로 계산)
    pubs = sorted(p["pub_date"] for p in items if p.get("pub_date"))
    return {
        "categories": [{"key": c["key"], "emoji": c["emoji"], "name": c["name"]}
                       for c in pcfg.CATEGORIES],
        "countries": [{"code": k, "emoji": v[0], "name": v[1]}
                      for k, v in pcfg.COUNTRY_LABEL.items()],
        "perWeek": [{"x": w, "y": per_week[w]} for w in sorted(per_week)],
        "pubRange": {"from": pubs[0], "to": pubs[-1]} if pubs else None,
        "totals": totals,
        "totalsAdjusted": adjusted,   # 표본 구성비로 깎은 총계(화면에서 근거를 밝힌다)
        "officeCounts": offices,
        "offices": [{"code": o["code"], "emoji": o["emoji"], "name": o["name"]}
                    for o in pcfg.OFFICES],
        "lookbackDays": pcfg.LOOKBACK_DAYS,
        "krLimit": pcfg.KR_LIMIT,
        "perApplicantLimit": pcfg.PER_APPLICANT_LIMIT,   # 표본 상한(랭킹의 '이상' 표기용)
        "applicants": len(pcfg.APPLICANTS),
        # 카드마다 붙는 조회 창구(Espacenet·Google Patents 등). 공개번호만 있으면
        # 되므로 항목별로 URL 을 저장하지 않고 템플릿만 한 번 내려보낸다.
        "links": ip_guide.links(),
        "items": items,
    }


def render_all(site_dir: Path, news_days: dict[str, dict],
               patent_weeks: dict[str, dict], generated: str,
               briefs: list[dict] | None = None,
               stats: dict | None = None,
               pbriefs: list[dict] | None = None) -> Path:
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    briefs = briefs or []
    pbriefs = pbriefs or []
    feed = {
        "generated": generated,
        "title": SITE_TITLE, "tagline": SITE_TAGLINE, "org": SITE_ORG,
        "orgUrl": SITE_ORG_URL, "club": SITE_CLUB,
        "brief": briefs[0] if briefs else None,   # 최신(홈 상단)
        "briefs": briefs,                          # 최신순 전체(타임라인)
        "patentBrief": pbriefs[0] if pbriefs else None,   # 최신(특허 탭 상단)
        "patentBriefs": pbriefs,                           # 최신순 전체
        "insights": _insights.build(news_days, patent_weeks),
        "news": _news_feed(news_days),
        "patents": _patent_feed(patent_weeks, stats),
        # 거래·지원 탭(수집과 무관한 사람 관리 상수 → ip_guide.py 에서만 고친다)
        "guide": ip_guide.guide(),        # 주소가 확인된 항목만(뼈대는 코드에만 남는다)
        "guideNote": ip_guide.NOTE,
        "trade": {"map": ip_guide.FIELD_MAP, "unpaired": ip_guide.UNPAIRED,
                  "conc": ip_guide.READ_CONC, "news": ip_guide.READ_NEWS,
                  "kr": ip_guide.READ_KR, "note": ip_guide.TRADE_NOTE},
    }
    payload = json.dumps(feed, ensure_ascii=False).replace("</", "<\\/")

    html = _PAGE.replace("__TITLE__", _esc(SITE_TITLE)) \
               .replace("__TAGLINE__", _esc(SITE_TAGLINE)) \
               .replace("__ORG__", _esc(SITE_ORG)) \
               .replace("__DEPT__", _esc(SITE_DEPT)) \
               .replace("__ORGURL__", _esc(SITE_ORG_URL)) \
               .replace("__CLUB__", _esc(SITE_CLUB)) \
               .replace("__CI__", _ci_markup()) \
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
.readcol{max-width:1120px}
.mast{border-bottom:3px solid var(--ink);padding-bottom:14px;margin-bottom:0;
  display:flex;align-items:flex-end;justify-content:space-between;gap:18px;flex-wrap:wrap}
.mast .masttext{min-width:0}
/* 운영 기관: 서비스명과 경쟁하지 않게 오른쪽 끝에 작게 */
.mast .org{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex-shrink:0;
  text-decoration:none;color:inherit}
.mast .org:hover .orgname{color:var(--accent2)}
.mast .org:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:6px}
.mast .org .ci{height:38px;width:auto;max-width:220px;display:block}
.mast .org .orgname{color:var(--muted);font-size:11.5px;font-weight:600;white-space:nowrap}
.mast .org .orgclub{color:var(--accent2);font-size:11px;font-weight:700;white-space:nowrap}
.mast .org:hover .orgclub{text-decoration:underline}
/* CI 는 흰 바탕에 쓰도록 만들어진 원색 로고다. 어두운 배경에 그대로 얹지 않고
   흰 판을 깔아 규정대로 흰 바탕 위에 놓이게 한다(라이트 모드 배경은 이미 거의 흰색). */
@media (prefers-color-scheme:dark){
  .mast .org .ci{background:#fff;padding:5px 8px;border-radius:6px;height:48px}
}
@media (max-width:640px){ .mast .org{align-items:flex-start} .mast .org .orgname{white-space:normal} }
.mast h1{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:0 0 3px}
/* 제목 자체가 홈으로 가는 버튼. 글자처럼 보이게 버튼 기본 스타일을 지운다 */
.mast h1 .brand{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;
  display:flex;gap:9px;align-items:center;text-align:left}
.mast h1 .brand:hover{color:var(--accent2)}
.mast h1 .brand:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:4px}
.mast h1 .bolt{color:var(--accent)}
.mast .tag{color:var(--muted);font-size:13px;margin:0}
.tabs{display:flex;gap:6px;margin:0 0 16px;border-bottom:1px solid var(--line)}
.tabs button{padding:11px 18px;font:inherit;font-weight:700;font-size:14.5px;color:var(--muted);
  background:none;border:0;border-bottom:3px solid transparent;margin-bottom:-1px;cursor:pointer}
.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
.tabs button:hover{color:var(--ink)}
/* 좁은 화면에서 탭이 넷이면 기본 여백으로는 글자가 두 줄로 접힌다 → 여백만 줄여
   한 줄을 유지한다(줄바꿈 금지는 걸지 않는다. 글자가 커지면 접히는 게 낫다) */
@media (max-width:560px){ .tabs{gap:2px} .tabs button{padding:11px 10px;font-size:13.5px} }
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
/* 특허 카드 하단의 조회 창구 링크. 제목 링크보다 약하게 보여 시선을 뺏지 않게 한다 */
.card .xlinks{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
.card .xl{font-size:11px;font-weight:700;color:var(--muted);text-decoration:none;
  border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:var(--bg);white-space:nowrap}
.card .xl:hover{color:var(--accent2);border-color:var(--accent2)}
/* 분야 지도(뉴스 비중 변화 × 권리 집중도). 색은 집중도 3단계의 단일 색상 램프다 —
   집중을 붉게, 분산을 푸르게 칠하면 기관이 분야에 좋고 나쁨을 매기는 것처럼 읽힌다.
   같은 파랑의 밝기 차이로만 정도를 보이고, 뜻은 글자(집중/중간/분산)가 진다.
   (밝은 화면은 진할수록 집중, 어두운 화면은 밝을수록 집중 — 표면 대비 기준.) */
:root{ --q1:#8FB2D6; --q2:#3A6FB0; --q3:#1D4470; }
@media (prefers-color-scheme:dark){ :root{ --q1:#33639C; --q2:#659ACF; --q3:#AFCFEE; } }
.qwrap{overflow-x:auto}
.qchart{display:block;width:100%;min-width:480px;height:auto}
.qchart .ax{stroke:var(--line);stroke-width:1}
.qchart .qz{fill:var(--muted);font-size:9.5px;font-weight:700;opacity:.65}
.qchart .qa{fill:var(--muted);font-size:10px;font-weight:700}
.qchart .ql{fill:var(--ink);font-size:10.5px;font-weight:700}
.qchart .dot{stroke:var(--card);stroke-width:2}
.qchart .hit{fill:transparent;cursor:default}
.qlegend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:8px 2px 0;
  font-size:11px;font-weight:700;color:var(--muted)}
.qlegend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.qlegend .s1 i{background:var(--q1)} .qlegend .s2 i{background:var(--q2)} .qlegend .s3 i{background:var(--q3)}
/* 분야별 상세 = 위 그림의 표 대응물(값을 그림에만 두지 않는다) */
.trow{border:1px solid var(--line);border-radius:11px;background:var(--card);padding:12px 14px;margin-top:10px}
.trow .th{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:14px;font-weight:800}
.trow .tb{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap;font-size:11px;font-weight:700}
.trow .tb span{border:1px solid var(--line);border-radius:999px;padding:2px 8px;color:var(--muted);
  font-variant-numeric:tabular-nums}
.trow .tr{margin:8px 0 0;font-size:12.5px;line-height:1.7;color:var(--muted);word-break:keep-all}
.trow .tk{margin:8px 0 0;display:flex;gap:5px;flex-wrap:wrap}
.trow .tcaveat{margin:7px 0 0;font-size:11px;line-height:1.6;color:var(--muted);opacity:.85}
/* 거래·지원 안내 */
#guide .gdesc{color:var(--muted);font-size:12.5px;line-height:1.7;margin:0 2px 10px}
#guide .glist{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
/* 항목이 하나뿐인 묶음에서 카드가 화면 폭 전체로 늘어나지 않게 상한을 둔다
   (2열일 때의 칸 너비와 비슷해 묶음끼리 크기가 들쭉날쭉해 보이지 않는다) */
#guide .gcard{display:block;text-decoration:none;color:inherit;background:var(--card);
  border:1px solid var(--line);border-radius:11px;padding:13px 15px;max-width:640px}
#guide .gcard:hover{border-color:var(--accent2)}
#guide .gname{font-size:14px;font-weight:800;display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
#guide .gorg{font-size:11px;font-weight:700;color:var(--muted)}
#guide .garr{margin-left:auto;color:var(--accent2);font-size:12px}
#guide .gwhat{margin:6px 0 0;font-size:12.5px;line-height:1.65;color:var(--muted);word-break:keep-all}
#guide .gnote{margin:22px 2px 0;padding-top:12px;border-top:1px solid var(--line);
  color:var(--muted);font-size:11.5px;line-height:1.7;word-break:keep-all}
/* 홈(대시보드) */
.homemode .controls,.homemode .resline,.homemode #overview,.homemode #results,.homemode #scope,
.homemode #more,.homemode #viewToggle{display:none!important}
.home{display:flex;flex-direction:column;gap:16px}
/* 섹션 구분(트렌드 인사이트 / 뉴스 / 특허). 한글이라 대문자 변환은 쓰지 않는다 */
.home .sec{font-size:13px;font-weight:800;color:var(--ink);letter-spacing:-.01em;
  margin:20px 2px 8px;padding-bottom:6px;border-bottom:2px solid var(--line)}
.home .sec:first-child{margin-top:4px}
.homekpi{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.homekpi .tile .v{font-size:21px;line-height:1.25}
.homekpi .tile small{display:block;font-size:11.5px;font-weight:600;color:var(--muted);margin-top:3px}
.homekpi .rgs{display:flex;gap:7px;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.homekpi .rgc{font-size:12px;font-weight:700;color:var(--ink)}
.homekpi .topap{font-size:15.5px;font-weight:800;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile .k{display:flex;align-items:center;gap:4px}
.tile .tq{font-size:11px;color:var(--muted);cursor:help;font-weight:700}
.tile .tq:hover{color:var(--accent2)}
.kpinote{color:var(--muted);font-size:11.5px;line-height:1.6;margin:9px 2px 0}
/* 오른쪽 '지난 브리핑'은 날짜+제목만 있으면 되므로 고정 폭으로 묶고, 남는 폭은 전부
   왼쪽 매트릭스에 준다(분야 이름이 길어져 넓을수록 가로 스크롤 없이 다 보인다). */
.homebot{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:14px;align-items:start}
.homebot.single{grid-template-columns:1fr}
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
.homemx{margin-top:14px}   /* 브리핑 아래 분야별 경쟁 구도 */
/* 홈 특허 섹션 = 브리핑 전문(접기 없음) */
.pbhome .pbw{font-size:11px;font-weight:600;color:var(--muted);margin-left:2px}
.pbhome .pbh{font-size:16px;font-weight:800;letter-spacing:-.01em;line-height:1.45;margin:4px 0 11px}
.pbhome .bbody{font-size:13px;line-height:1.75}
.pbhome .bbody p{margin:0 0 7px}
.pbhome .bsec{margin:0 0 12px}
.pbhome .bsl{font-size:12px;font-weight:800;color:var(--accent2);margin:0 0 5px;
  padding-bottom:4px;border-bottom:1px solid var(--line)}
.pbhome .bpoints{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:11px}
.pbhome .pt{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.pbhome .pt .pl{font-size:12px;font-weight:800;display:flex;align-items:center;gap:5px;margin-bottom:3px}
.pbhome .pt .px{font-size:11.5px;color:var(--muted);line-height:1.5}
.pbhome .pbfoot{color:var(--muted);font-size:11px;margin:11px 0 0;padding-top:9px;
  border-top:1px solid var(--line)}
@media (max-width:1100px){ .pbhome .bpoints{grid-template-columns:1fr} }
/* 해외 출원인의 국내 공개 */
.krwrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.krow{min-width:0}
.kap{font-size:13px;font-weight:800;display:flex;align-items:center;gap:6px;
  padding-bottom:5px;margin-bottom:7px;border-bottom:1px solid var(--line)}
.kap .kcnt{margin-left:auto;font-size:11px;font-weight:700;color:var(--muted)}
.klist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px}
.klist li{font-size:12.5px;line-height:1.5}
.klist a{color:var(--ink);text-decoration:none}
.klist a:hover{color:var(--accent2);text-decoration:underline}
.klist .kc{display:inline-block;margin-left:6px;font-size:10.5px;font-weight:700;color:var(--muted);
  border:1px solid var(--line);border-radius:5px;padding:1px 5px;white-space:nowrap}
.klist .kn{display:block;font-size:10.5px;color:var(--muted);margin-top:1px}
.klist .kmore{color:var(--muted);font-size:11.5px}
/* 국내/해외 소제목 구분 */
.brief .bsec{margin:0 0 13px}
.brief .bsec:last-child{margin-bottom:0}
.brief .bsl{font-size:12px;font-weight:800;color:var(--accent2);letter-spacing:.02em;
  margin:0 0 5px;padding-bottom:4px;border-bottom:1px solid var(--line)}
.pbpast{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.pbpast summary{font-size:12px;font-weight:700;color:var(--muted);cursor:pointer}
.pbpast summary:hover{color:var(--ink)}
.pbpast .timeline{margin-top:10px}
@media (max-width:1100px){ .homebot{grid-template-columns:1fr} }
@media (max-width:720px){ .homekpi{grid-template-columns:repeat(2,1fr)} }
/* 트렌드 인사이트 바 */
.insights{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:12px;margin:2px 0 16px}
.insights.two{grid-template-columns:1.15fr 1fr}   /* '이번 주 공개 특허'를 특허 섹션으로 뺀 뒤 */
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
.card .meta .off{border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px}
.card .meta .cpc{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--accent2)}
.card .sum{color:var(--muted);font-size:13px;margin-top:6px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-left:auto}
.card.isnew{border-left:3px solid var(--accent)}
.mockflag{background:var(--accent);color:#1a1a1a;font-size:10.5px;font-weight:700;padding:1px 7px;border-radius:999px}
.empty{color:var(--muted);font-size:14px;padding:40px 0;text-align:center;border:1px dashed var(--line);border-radius:9px}
.scope,.scopewrap{color:var(--muted);font-size:12px;line-height:1.6;margin:0 2px 12px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 11px}
.scope b{color:var(--ink)}
.stats .panel .scope{margin:0 0 12px}
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
.rankseg{margin-left:auto;display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.rankseg button{font:inherit;font-size:11.5px;font-weight:600;color:var(--muted);background:var(--card);border:0;padding:4px 10px;cursor:pointer}
.rankseg button[aria-pressed="true"]{background:var(--ink);color:var(--bg)}
.rgrank{margin-bottom:13px}
.rgrank:last-child{margin-bottom:0}
.rgrank .rghead{font-size:12px;margin-bottom:5px}
.stats .sub{color:var(--muted);font-size:12px;margin:0 0 13px}
.lead{display:flex;flex-direction:column;gap:9px}
.lead .row{display:grid;grid-template-columns:170px 1fr auto;align-items:center;gap:10px}
.lead .nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lead .nm .rk{color:var(--muted);font-weight:700;margin-right:6px;font-variant-numeric:tabular-nums}
.lead .bar{height:15px;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}
.lead .bar.cap{background:repeating-linear-gradient(135deg,var(--accent) 0 7px,var(--line) 7px 10px)}
.lead .val .plus{color:var(--muted);font-weight:700;margin-left:1px}
.lead .val{font-size:12.5px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.lead .val .co{color:var(--muted);font-weight:500;font-size:11.5px;margin-left:5px}
.pmxwrap{overflow-x:auto}
.pmx{border-collapse:separate;border-spacing:2px;font-size:12.5px;min-width:100%}
.pmx th{font-weight:600;color:var(--muted);text-align:center;padding:3px 4px;font-size:13px;white-space:nowrap}
.pmx th.cnr{text-align:left;font-size:11px;font-weight:600}
/* 분야 머리글: 아이콘 대신 이름. <wbr> 위치(가운뎃점)에서만 접힌다 */
.pmx th.cth{font-size:10.5px;font-weight:600;line-height:1.25;white-space:normal;word-break:keep-all;
  max-width:80px;vertical-align:bottom}
.pmx th.cth .seg{white-space:nowrap}
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
/* 분야별 집중도(상위 3곳 점유율). 막대 하나로 분야끼리 눈으로 비교되게 한다 */
.catlead .clab{display:flex;align-items:center;gap:8px}
.catlead .conc{display:flex;align-items:center;gap:7px;margin-left:auto;font-size:11px;font-weight:700}
.catlead .cbar{width:96px;height:6px;border-radius:999px;background:var(--line);overflow:hidden;flex:none}
/* 막대 색은 분야 지도(.qchart)와 같은 단일 색상 램프를 쓴다 — 같은 변수를 두 화면에서
   다른 색 언어로 칠하면 읽는 사람이 둘을 다른 지표로 오해한다. 뜻은 글자가 지므로
   글자는 색 램프가 아니라 본문 색을 입는다(연한 단계는 글자로 쓰기엔 대비가 모자란다). */
.catlead .cbar i{display:block;height:100%;border-radius:999px;background:var(--muted)}
.catlead .conc.hi .cbar i{background:var(--q3)}
.catlead .conc.mid .cbar i{background:var(--q2)}
.catlead .conc.lo .cbar i{background:var(--q1)}
.catlead .cpct{font-variant-numeric:tabular-nums;color:var(--ink)}
.catlead .clv{color:var(--ink)}
.catlead .cn{color:var(--muted);font-weight:600}
.catlead .conc.na{color:var(--muted)}
@media (max-width:560px){ .catlead .conc{margin-left:0;width:100%} .catlead .clab{flex-wrap:wrap} }
.statkpi{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:4px}
.statkpi .k{color:var(--muted);font-size:11.5px}
.statkpi .v{font-size:19px;font-weight:800}
.unknown{color:var(--muted);font-size:12px;margin-top:8px}
.homehint{color:var(--muted);font-size:12.5px;margin:2px 0 0}
@media (max-width:820px){
.stats{grid-template-columns:1fr}
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
    <div class="masttext">
      <h1><button type="button" id="brand" class="brand" aria-label="__TITLE__ — 홈으로"><span class="bolt">⚡</span> __TITLE__</button></h1>
      <p class="tag">__TAGLINE__</p>
    </div>
    <a class="org" href="__ORGURL__" target="_blank" rel="noopener"
       title="__CLUB__ 페이지로 이동 — __ORG__ 운영">__CI__<span class="orgname">__DEPT__</span><span
       class="orgclub">__CLUB__ ↗</span></a>
  </header>
  <nav class="tabs" role="tablist" aria-label="보기 전환">
    <button role="tab" id="tab-home" aria-selected="true" data-tab="home">🏠 홈</button>
    <button role="tab" id="tab-news" aria-selected="false" data-tab="news">📰 뉴스</button>
    <button role="tab" id="tab-patents" aria-selected="false" data-tab="patents">📄 특허</button>
    <button role="tab" id="tab-guide" aria-selected="false" data-tab="guide">🤝 거래·지원</button>
  </nav>
  <section class="home" id="home" aria-label="대시보드" hidden></section>
  <section class="home" id="guide" aria-label="지식재산 거래·지원 안내" hidden></section>
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
  <p class="scopewrap" id="scope" hidden></p>
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
// 링크 주소는 http(s) 만 허용한다. 제목·요약은 esc 로 막히지만 href 는 esc 를 통과해도
// javascript: 스킴이 그대로 남아 클릭 한 번으로 실행된다(RSS·OPS 응답은 외부 입력이다).
const safeUrl = u => { const s=String(u==null?'':u).trim(); return /^https?:\/\//i.test(s) ? s : ''; };
const LS_KEY = 'pnp_lastVisit';
const lastVisit = Number(localStorage.getItem(LS_KEY) || 0);


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
    // 출원인은 매트릭스·랭킹과 같은 정규화명(aName)으로 통일. 원문이 다르면 툴팁으로.
    if(it.aName) bits.push('<span class="src" title="'+esc(it.assignee||it.aName)+'">'
      + (it.aFlag||'') + ' ' + esc(it.aName)+'</span>');
    if(it.office) bits.push('<span class="off" title="공개 특허청">'+esc(it.office)+' 공보</span>');
    if(it.number) bits.push('<span class="num">'+esc(it.number)+'</span>');
    if(it.cpc && it.cpc.length) bits.push('<span class="cpc" title="CPC 분류(분야 판정 근거)">'
      + esc(it.cpc.join(' ')) + '</span>');
    if(it.pub_date) bits.push('<span class="mono">공개 '+esc(it.pub_date)+'</span>');
  }
  const mock = it.mock ? ' <span class="mockflag">샘플</span>' : '';
  const meta = bits.join(' <span aria-hidden="true">·</span> ');
  const sum = it.summary ? '<div class="sum">'+hl(it.summary)+'</div>' : '';
  const isS = saved.has(it.url), isR = read.has(it.url);
  return '<article class="card'+(nw?' isnew':'')+(isR?' isread':'')+'">'
    + '<button class="star'+(isS?' on':'')+'" data-save="'+esc(it.url)+'" aria-label="저장" title="저장">'+(isS?'★':'☆')+'</button>'
    + '<a class="t" href="'+esc(safeUrl(it.url))+'" target="_blank" rel="noopener" data-read="'+esc(it.url)+'">'+hl(it.title||'(제목 없음)')+'</a>'
    + '<div class="meta">'+meta+mock+'<span class="tag">'+(c.emoji||'')+' '+esc(c.name)+'</span></div>'
    + sum + (state.tab==='patents' ? patLinks(it) : '') + '</article>';
}

// 특허 카드 → 조회 창구. 공개번호 하나로 여러 곳을 열 수 있어 URL 은 저장하지 않고
// FEED.patents.links 의 템플릿({n})에 끼워 넣는다. office 가 지정된 링크는 그
// 특허청 공보에만 붙는다(예: KIPRIS 는 KR 공보에만).
function patLinks(it){
  const L = (FEED.patents.links)||[];
  if(!L.length || !it.number) return '';
  const n = encodeURIComponent(it.number);
  const out = L.filter(l=> !l.office || l.office===it.office).map(l=>
    '<a class="xl" href="'+esc(safeUrl(String(l.url||'').replace('{n}', n)))+'"'
    + ' target="_blank" rel="noopener" title="'+esc(l.tip||'')+'">'+esc(l.label)+' ↗</a>');
  return out.length ? '<div class="xlinks">'+out.join('')+'</div>' : '';
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
    + (foot.length?'<div class="bfoot">'+foot.join(' ')+'</div>':'')
    + '</div>';   // ← .brief 닫기. 없으면 홈의 후속 블록이 전부 이 카드 안에 중첩된다.
}

function insightsHTML(){
  const ins = FEED.insights;
  if(!ins || !ins.asOf) return '';
  const w = ins.window || {recentDays:7, recentWeeks:4};

  // 비교할 '이전' 구간에 기사가 하나도 없으면(아카이브를 막 시작한 때) 증감을 숨긴다.
  // 안 그러면 prev 가 전부 0 이라 모든 항목이 전량 급증한 것처럼 보인다.
  const cmp = ins.comparable !== false;

  // 1) 최근 많이 언급된 키워드 (클릭 → 검색)
  const kws = (ins.trending||[]);
  const kwHtml = kws.length ? kws.map(k=>{
    const up = (cmp && k.rising) ? '<span class="up" title="이전 대비 증가">▲'+(k.count-k.prev)+'</span>' : '';
    return '<button class="kw'+((cmp&&k.rising)?' hot':'')+'" data-kw="'+esc(k.term)+'" title="'+esc(k.term)
      +' — 검색">'+esc(k.term)+'<span class="c">'+k.count+'</span>'+up+'</button>';
  }).join('') : '<span class="iempty">데이터가 쌓이면 표시됩니다.</span>';

  // 2) 이슈 흐름 (카테고리 최근 vs 이전)
  const ct = (ins.catTrend||[]).slice(0,6);
  const ctHtml = ct.length ? ct.map(r=>{
    const d=r.delta; const cls=d>0?'up':(d<0?'dn':'fl'); const sym=d>0?'▲':(d<0?'▼':'–');
    const dd=(!cmp || d===0)?'':(' '+sym+Math.abs(d));
    return '<div class="row" data-cat="'+esc(r.key)+'" title="'+esc(r.name)+' 필터"><div class="nm">'
      +r.emoji+' '+esc(r.name)+'</div><div class="d">'+r.recent+'<span class="n">건</span>'
      +'<span class="'+(cmp?cls:'fl')+'">'+dd+'</span></div></div>';
  }).join('') : '<span class="iempty">–</span>';

  const kwSub = cmp ? '최근 '+w.recentDays+'일 뉴스 제목 · <b>▲</b>=이전 대비 증가 · 눌러서 검색'
                    : '최근 '+w.recentDays+'일 뉴스 제목 · 눌러서 검색 (비교할 이전 기간이 아직 쌓이지 않아 증감은 표시하지 않습니다)';
  const ctSub = cmp ? '카테고리별 최근 '+w.recentDays+'일 새 기사 (이전 대비) · 눌러서 필터'
                    : '카테고리별 최근 '+w.recentDays+'일 새 기사 · 눌러서 필터';

  // '이번 주 공개 특허'는 아래 특허 섹션으로 옮겼다 — 인사이트는 뉴스 기반 둘만 둔다.
  return '<div class="insights two">'
    + '<div class="ipanel"><h3>🔥 요즘 뜨는 키워드</h3>'
    + '<p class="isub">'+kwSub+'</p>'
    + '<div class="kwrap">'+kwHtml+'</div></div>'
    + '<div class="ipanel"><h3>📈 이슈 흐름</h3>'
    + '<p class="isub">'+ctSub+'</p>'
    + '<div class="trend">'+ctHtml+'</div></div></div>';
}

// 이번 주 공개 특허 — 인사이트에 있던 것을 특허 섹션으로 옮겼다(질적 노출, 건수 아님).
// 홈 특허 섹션의 출원인×분야 요약(지역별 상위 3곳). 특허 탭 통계 뷰에는 현재 필터가
// 반영된 전체 매트릭스가 있고, 여기 것은 브리핑 서술을 수치로 받쳐 주는 용도다.
// 홈의 특허 요약. 예전엔 출원인×분야 매트릭스를 줄여 놓았는데, 지역별 상위 3곳만
// 남기면 표가 커서 자리는 많이 먹으면서 '그래서 뭐' 가 남지 않았다. 같은 데이터로
// '이 분야를 몇 곳이 나눠 갖고 있나'를 답하는 쪽이 한 화면에서 더 쓸모 있다.
// 전체 매트릭스는 특허 탭 통계 뷰에 그대로 있고, 아래 링크로 바로 간다.
function concPanelHTML(){
  const list=FEED.patents.items; if(!list.length) return '';
  const rows=concentration(list); if(!rows.length) return '';
  const r=FEED.patents.pubRange;
  return '<div class="homepanel"><h3>🧭 분야별 경쟁 구도'
    + '<span class="morelink" data-go="patents-stats">특허 통계 전체 →</span></h3>'
    + '<p class="sub">각 기술분야를 <b>몇 곳이 나눠 갖고 있는지</b>입니다. 막대는 상위 3곳의 몫, '
    + '‘실질 N곳’은 규모 차이를 반영한 경쟁자 수, 칩의 %는 분야 안에서의 지분입니다.'
    + (r? '<br>공개일 <b>'+esc(r.from)+' ~ '+esc(r.to)+'</b> · 추적 중인 주요 출원인 '
         +(FEED.patents.applicants||0)+'곳 기준 추정이며 시장 점유율이 아닙니다.' : '')
    + '</p>'
    + '<div class="catlead">'+concRowsHTML(rows)+'</div></div>';
}

function patentPickPanelHTML(){
  const picks = patentPicks(8);
  if(!picks.length) return '';
  const pkHtml = picks.map(p=>{
    // 국기는 매트릭스와 같은 출원인 국적(aFlag)으로 통일 — 같은 화면에서 달라 보이지 않게.
    const who = p.aName ? '<span class="who">'+esc(p.aName)+'</span>' : '';
    return '<a class="pk" href="'+esc(safeUrl(p.url))+'" target="_blank" rel="noopener" title="'+esc(p.title)+'">'
      +'<span class="pf">'+(p.aFlag||'📄')+'</span>'
      +'<span class="pt2">'+esc(p.title||'(제목 없음)')+who+'</span></a>';
  }).join('');
  return '<div class="homepanel"><h3>📄 이번 주 공개 특허</h3>'
    + '<p class="sub">최근 공개분 일부 · 무엇을 누가 출원했는지(건수 아님) · 누르면 원문.</p>'
    + '<div class="ppick">'+pkHtml+'</div></div>';
}

// 홈에서는 특허 브리핑을 접기 없이 전문으로 보여준다(특허 탭 배너와 달리 토글 없음).
function patentBriefHomeHTML(){
  const b=FEED.patentBrief;
  if(!b || !(b.headline || (b.sections&&b.sections.length) || (b.body&&b.body.length))) return '';
  const body = (b.sections&&b.sections.length)
    ? b.sections.map(s=>'<div class="bsec"><div class="bsl">'+esc(s.label||'')+'</div>'
        + (s.paras||[]).map(p=>'<p>'+esc(p)+'</p>').join('') + '</div>').join('')
    : (b.body||[]).map(p=>'<p>'+esc(p)+'</p>').join('');
  const pts=(b.points||[]).map(p=>'<div class="pt"><div class="pl">'+esc(p.emoji||'')+' '+esc(p.label||'')
    +'</div><div class="px">'+esc(p.text||'')+'</div></div>').join('');
  return '<div class="homepanel pbhome"><h3>🔬 이번 주 특허 브리핑'
    + (b.week? '<span class="pbw">'+esc(b.week)+' 수집분</span>':'')
    + '<span class="morelink" data-go="patents">특허 탭 →</span></h3>'
    + (b.headline?'<h4 class="pbh">'+esc(b.headline)+'</h4>':'')
    + '<div class="bbody">'+body+'</div>'
    + (pts?'<div class="bpoints">'+pts+'</div>':'')
    + pastPatentBriefsHTML()
    + (b.basis?'<p class="pbfoot">'+esc(b.basis)+'</p>':'')
    + '</div>';
}

// 지난 특허 브리핑 — 특허 탭 배너를 없애면서 이리로 옮겼다(접어 둔다).
function pastPatentBriefsHTML(){
  const past=(FEED.patentBriefs||[]).slice(1);
  if(!past.length) return '';
  return '<details class="pbpast"><summary>지난 특허 브리핑 '+past.length+'건</summary>'
    + '<div class="timeline">'
    + past.map(x=>{
        const ps=(x.sections&&x.sections.length)? x.sections.flatMap(s=>s.paras||[]) : (x.body||[]);
        return '<div class="tl"><div class="tld">'+esc(x.week||'')+' 수집</div>'
          + '<div class="tlh">'+esc(x.headline||'(제목 없음)')+'</div>'
          + '<div class="tlb">'+ps.slice(0,2).map(p=>'<p>'+esc(p)+'</p>').join('')+'</div></div>';
      }).join('')
    + '</div></details>';
}

function kpiHTML(){
  const n=FEED.news, p=FEED.patents;
  const nL = n.perDay.length? n.perDay[n.perDay.length-1] : {x:'-',y:0};
  // 특허 요약 지표(출원인 수·국적 내역·최다 출원인)는 통계 탭까지 안 들어가도 보이게 홈에.
  const ranked = p.items.length? _rankApplicants(p.items) : [];
  const regCnt={}; ranked.forEach(r=>{ regCnt[r.region]=(regCnt[r.region]||0)+1; });
  const regChips = p.countries.map(rg=>regCnt[rg.code]
      ? '<span class="rgc">'+rg.emoji+regCnt[rg.code]+'</span>' : '').filter(Boolean).join('');
  const top = ranked[0];
  const lookback = p.lookbackDays||90;
  const nAp = p.applicants||0;      // 설정에 등록된 출원인 수(수집 대상)
  return '<div class="homekpi">'
    + tile('📰 뉴스 누적', n.items.length.toLocaleString()
        + '<small>건 · 최근 '+esc(nL.x)+' '+nL.y+'건</small>',
        '수집을 시작한 뒤 쌓인 전체 기사 수입니다. Google 뉴스 RSS 를 매일 조회해 새 기사만 더합니다.')
    + tile('📄 특허 누적', p.items.length.toLocaleString()
        + (p.pubRange? '<small>건 · 공개 '+esc(p.pubRange.from)+' ~ '+esc(p.pubRange.to)+'</small>':'건'),
        '아카이브에 저장된 특허 문헌 수입니다. 매주 최근 '+lookback+'일 공개분을 조회해 새 것만 더하며, '
        + '출원인당 저장 상한이 있어 대형 출원인은 전수가 아니라 표본입니다.')
    + tile('🏢 분석 출원인', (ranked.length||0)
        + '<small class="rgs">'+regChips+'</small>',
        '수집 대상으로 등록한 출원인은 '+nAp+'곳이고, 그중 지금 아카이브에 문헌이 있는 곳이 '
        + (ranked.length||0)+'곳입니다. 국기 옆 숫자는 국적별 출원인 수입니다.')
    + tile('🏆 최다 출원인', top
        ? '<span class="topap">'+(top.flag||'')+' '+esc(top.name)+'</span><small>'
          + top.total.toLocaleString()+'건 · 최근 '+lookback+'일 전 세계 공개</small>'
        : '—',
        '산출 근거: 등록한 출원인 '+nAp+'곳 가운데, 최근 '+lookback+'일 사이 전력 CPC 로 공개된 문헌이 '
        + '가장 많은 곳입니다. 유럽특허청(EPO)이 90여 개 관할 구역에서 모아 수록한 서지 데이터를 '
        + '조회하며, IP5 나 국내(KR) 로 한정하지 않습니다. 다만 관청마다 수록 범위·시점이 달라 최근 공개분에는 '
        + '시차가 있을 수 있고, 출원인 이름 표기가 다르면 누락될 수 있습니다. 같은 발명이 여러 나라에 '
        + '공개되면 각각 세므로 특허 패밀리 수가 아니라 공개 문헌 수이고, 조회 조건이 전력 CPC 라 '
        + '배터리처럼 해당 CPC 에 출원이 몰리는 분야가 크게 잡힙니다. 기업의 기술력이나 시장 점유율을 '
        + '뜻하지 않습니다.')
    + '</div>'
    // 툴팁은 모바일에서 뜨지 않는다 → 특허 수치의 산출 근거는 한 줄로도 항상 보이게.
    // 출원인 범위(40곳)는 바로 옆 '분석 출원인' 타일과 ⓘ 툴팁에 있어 여기선 뺀다.
    + '<p class="kpinote">특허 수치는 <b>90여 개 관할 구역의 공개 데이터</b>(유럽특허청 수록 기준)입니다. '
    + '최근 '+lookback+'일 공개분을 전력 CPC 로 조회한 값입니다. 같은 발명이 여러 나라에 공개되면 각각 '
    + '세므로 특허 패밀리 수가 아니라 공개 문헌 수입니다.</p>';
}



function timelineHTML(){
  const past=(FEED.briefs||[]).slice(1);   // 최신은 위에 크게 노출, 나머지를 타임라인으로
  const inner = past.length ? past.map(b=>{
    const body=(b.body||[]).slice(0,2).map(p=>'<p>'+esc(p)+'</p>').join('');
    return '<div class="tl"><div class="tld">'+esc(b.date||'')+'</div>'
      + '<div class="tlh">'+esc(b.headline||'(제목 없음)')+'</div>'
      + '<div class="tlb">'+body+'</div></div>';
  }).join('') : '<p class="homehint">지난 브리핑이 쌓이면 여기 타임라인으로 보여요(매주 갱신).</p>';
  return '<div class="homepanel"><h3>🗓️ 지난 브리핑</h3>'
    + '<p class="sub">제목을 누르면 요지가 펼쳐집니다.</p>'
    + '<div class="timeline">'+inner+'</div></div>';
}

// 홈 구성: 아카이브 현황(숫자) → 트렌드 인사이트(계산) → 뉴스 → 특허.
// 주제별로 묶어 읽히게 한다. 예전에는 뉴스 브리핑과 특허 브리핑 사이에 KPI·인사이트가
// 끼어 있어 어디까지가 뉴스 얘기인지 흐렸다.
function renderHome(){
  const parts=[];
  parts.push(kpiHTML());
  const ih=insightsHTML(); if(ih) parts.push('<div class="sec">🔎 트렌드 인사이트</div>'+ih);

  // 📰 뉴스 — 브리핑 전문 + 지난 브리핑 타임라인.
  // 지난 브리핑이 아직 없으면 2열 배치가 절반을 빈칸으로 남긴다 → 그땐 1열로.
  const bh=briefHTML();
  if(bh){
    const hasPast=(FEED.briefs||[]).length>1;
    parts.push('<div class="sec">📰 뉴스</div>'
      + '<div class="homebot'+(hasPast?'':' single')+'">'
      + bh + (hasPast? timelineHTML() : '') + '</div>');
  }

  // 📄 특허 — 브리핑 전문 + 이번 주 공개 특허를 두 칸으로, 그 아래 분야별 경쟁 구도를
  // 전체 폭으로. 브리핑이 서술한 내용을 바로 아래 수치가 받아 이어서 읽힌다.
  const pb=patentBriefHomeHTML(), pk=patentPickPanelHTML(), mx=concPanelHTML();
  if(pb||pk||mx){
    parts.push('<div class="sec">📄 특허</div>'
      + ((pb||pk)? '<div class="homebot'+((pb&&pk)?'':' single')+'">'+(pb||'')+(pk||'')+'</div>' : '')
      + (mx? '<div class="homemx">'+mx+'</div>' : ''));
  }
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

// 특허가 '어느 기간 공개분'인지 한 줄로. 주별 버킷은 수집한 주라서 공개일과 다르다.

function patentScopeHTML(){
  const f=FEED.patents, r=f.pubRange;
  if(!r) return '';
  return '<p class="scope">📄 <b>공개일 '+esc(r.from)+' ~ '+esc(r.to)+'</b> 특허 '
    + f.items.length.toLocaleString()+'건 · 주요 출원인 '+(f.applicants||0)+'곳을 매주 조회해'
    + '(1회 조회 범위: 최근 '+(f.lookbackDays||90)+'일 공개분) 새로 공개된 것만 누적합니다.</p>';
}

function renderOverview(){
  const f = FEED[state.tab];
  const total = f.items.length;
  const series = state.tab==='news' ? f.perDay : f.perWeek;
  const periods = series.length;
  const latest = series.length ? series[series.length-1] : {x:'-',y:0};
  const newCount = f.items.filter(isNew).length;
  const news = state.tab==='news';
  const ov = $('#overview');
  // 특허는 '최근 주(수집한 주)' 대신 공개일 범위를 보여준다 — 그쪽이 사용자가 궁금한 값.
  const thirdTile = news
    ? tile('최근 일', latest.y+'<small>건 · '+esc(latest.x)+'</small>')
    : tile('공개일 범위', (f.pubRange
        ? '<small>'+esc(f.pubRange.from)+' ~ '+esc(f.pubRange.to)+'</small>' : '—'));
  ov.innerHTML =
    tile('누적 '+(news?'기사':'특허'), total.toLocaleString())
    + tile('수집 '+(news?'일':'주'), periods)
    + thirdTile
    + (lastVisit
        ? tile('✨ 새 항목', newCount + '<small>지난 방문 이후</small>')
        : tile(news? '최근 7일' : '최근 4주',
            f_recentCount(f, news) + '<small>건</small>'))
    + (periods>1
        ? '<div class="tile spark sparkwrap"><div class="k">'+(news?'일별':'주별')+' 수집 추이 <b>('+periods+')</b></div>'
          + sparkline(series) + '</div>'
        : '');
}
// 최근 구간 합계(뉴스=7일, 특허=4주) — '오늘 열람' 자리를 채울 실질 지표
function f_recentCount(f, news){
  const series = news? f.perDay : f.perWeek;
  return series.slice(news? -7 : -4).reduce((a,b)=>a+b.y,0).toLocaleString();
}
// tip 을 주면 라벨 옆에 ⓘ 를 달고 마우스를 올렸을 때 산출 근거를 보여준다.
function tile(k,v,tip){
  const q = tip? '<span class="tq" title="'+esc(tip)+'">ⓘ</span>' : '';
  return '<div class="tile"><div class="k">'+k+q+'</div><div class="v mono">'+v+'</div></div>';
}

function renderChips(){
  if(state.tab==='home'||state.tab==='guide'){ $('#catChips').innerHTML=''; const cc=$('#countryChips'); cc.hidden=true; cc.innerHTML=''; return; }
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
  if(state.tab==='patents'){
    // 특허 탭엔 기간 프리셋이 없지만, 스파크라인 클릭으로 특정 주가 걸릴 수 있다 →
    // 해제할 수 있게 그때만 칩을 보여준다.
    if(state.period==='all'){ pb.hidden=true; pb.innerHTML=''; return; }
    pb.hidden=false;
    pb.innerHTML='<button class="f" data-period="all">전체 기간</button>'
      + '<button class="f" data-period="'+esc(state.period)+'" aria-pressed="true">📅 '
      + esc(state.period)+' 주 ✕</button>';
    return;
  }
  if(state.tab!=='news'){ pb.hidden=true; pb.innerHTML=''; return; }
  pb.hidden=false;
  const opts=[['all','전체'],['today','오늘'],['7d','최근 7일'],['30d','최근 30일']];
  let html=opts.map(o=>'<button class="f" data-period="'+o[0]+'" aria-pressed="'+(state.period===o[0])+'">'+o[1]+'</button>').join('');
  if(state.period!=='all' && !opts.some(o=>o[0]===state.period))
    html+='<button class="f" data-period="'+esc(state.period)+'" aria-pressed="true">📅 '+esc(state.period)+' ✕</button>';
  pb.innerHTML=html;
}
function syncSearchPlaceholder(){
  const q=$('#q'); if(!q) return;
  q.placeholder = state.tab==='patents'
    ? '특허 제목·출원인·공개번호·CPC 검색'
    : '뉴스 제목·요약·언론사 검색';
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

// ── 거래 판단 참고: 뉴스 비중 변화 × 권리 집중도 ──────────────────────
// 두 축을 겹쳐 봐야 답이 나온다. 관심이 느는데 권리가 소수에 몰렸으면 회피설계·
// 라이선스가 먼저고, 관심이 느는데 권리가 흩어져 있으면 자체 출원 여지가 있다.
// 예측은 하지 않는다 — 지금 어떤 모양인지까지만 보인다(FEED.trade.note).
function tradeRows(){
  const T=FEED.trade||{}, MAP=T.map||{};
  const conc=concentration(FEED.patents.items||[]);
  const ct={}; (FEED.insights.catTrend||[]).forEach(c=>ct[c.key]=c);
  // 해외 출원인이 국내(KR)에 공개한 건 — 국내에서 실제로 부딪힐 수 있는 권리다.
  const kr={};
  (FEED.patents.items||[]).forEach(it=>{ if(it.office!=='KR'||it.aCountry==='KR') return;
    const s=kr[it.category]||(kr[it.category]=new Set()); s.add((it.aFlag||'')+' '+it.aName); });
  const cmp=FEED.insights.comparable;
  return conc.filter(r=>r.cat.key in MAP).map(r=>{
    const c=ct[r.cat.key], ratio=(c&&c.ratio!=null)?c.ratio:null;
    const dir = (!cmp||ratio==null) ? 'flat'
      : ratio>=1.10 ? 'up' : ratio<=0.90 ? 'down' : 'flat';
    const lv = r.n<CONC_MIN ? null : (r.neff<5?'hi':r.neff<8?'mid':'lo');
    return {r, news:c||null, ratio, dir, lv,
            kr:[...(kr[r.cat.key]||[])].sort(),
            note: MAP[r.cat.key]||''};
  });
}

// 분야 지도. x=뉴스 비중 배율(log2, 가운데가 '변화 없음'), y=실질 경쟁자 수(위가 집중),
// 원 크기=그 분야 추정 규모. 값은 전부 아래 표에도 있다(그림에만 두지 않는다).
const QW=680, QH=340, QPAD={l:52,r:18,t:16,b:40}, QCLAMP=1.6;
function quadChartHTML(rows){
  const pts=rows.filter(d=>d.lv && d.ratio!=null);
  if(pts.length<3) return '';
  const x0=QPAD.l, x1=QW-QPAD.r, y0=QPAD.t, y1=QH-QPAD.b;
  const lg=v=>Math.max(-QCLAMP, Math.min(QCLAMP, Math.log2(v)));
  const px=v=>x0+(lg(v)+QCLAMP)/(QCLAMP*2)*(x1-x0);
  const NEF=[2,12];                          // 실질 경쟁자 수 축(고정 → 주마다 비교 가능)
  const py=v=>y0+(Math.max(NEF[0],Math.min(NEF[1],v))-NEF[0])/(NEF[1]-NEF[0])*(y1-y0);
  const maxTot=Math.max(...pts.map(d=>d.r.tot));
  const pr=t=>9+Math.sqrt(t/maxTot)*13;
  const col=lv=>lv==='hi'?'var(--q3)':lv==='mid'?'var(--q2)':'var(--q1)';
  const xm=px(1), ym=py(6);
  let s='<svg class="qchart" viewBox="0 0 '+QW+' '+QH+'" role="img" '
    + 'aria-label="분야별 뉴스 비중 변화와 권리 집중도 지도">';
  // 사분면 안내(눈금선은 실선 헤어라인 하나씩만)
  s+='<line class="ax" x1="'+xm+'" y1="'+y0+'" x2="'+xm+'" y2="'+y1+'"/>'
   + '<line class="ax" x1="'+x0+'" y1="'+ym+'" x2="'+x1+'" y2="'+ym+'"/>'
   + '<line class="ax" x1="'+x0+'" y1="'+y1+'" x2="'+x1+'" y2="'+y1+'"/>';
  const zone=(tx,ty,t,anc)=>'<text class="qz" x="'+tx+'" y="'+ty+'" text-anchor="'+anc+'">'+t+'</text>';
  s+=zone(x0+6,y0+13,'관심 ↓ · 권리 집중 — 성숙·고착','start')
   + zone(x1-6,y0+13,'관심 ↑ · 권리 집중 — 회피설계·라이선스 먼저','end')
   + zone(x0+6,y1-8,'관심 ↓ · 권리 분산 — 관망','start')
   + zone(x1-6,y1-8,'관심 ↑ · 권리 분산 — 자체 출원 여지','end');
  // 축 이름·눈금
  s+='<text class="qa" x="'+((x0+x1)/2)+'" y="'+(QH-8)+'" text-anchor="middle">'
   + '← 뉴스 비중 줄어듦   |   늘어남 →</text>'
   + '<text class="qa" x="'+(x0-8)+'" y="'+(y0+10)+'" text-anchor="end">집중</text>'
   + '<text class="qa" x="'+(x0-8)+'" y="'+(y1-2)+'" text-anchor="end">분산</text>'
   + '<text class="qa" x="'+(x0-8)+'" y="'+(ym+4)+'" text-anchor="end">실질 6곳</text>';
  // 라벨은 원마다 붙는다(값이 아니라 이름이므로 전부 붙어도 된다). 다만 점이 몰리면
  // 라벨이 옆 원을 덮는다 → 오른쪽·왼쪽·위·아래 순으로 비어 있는 자리를 찾아 놓고,
  // 넷 다 막히면 원 위쪽에 얹는다. 글자 폭은 한글 기준으로 어림한다(측정 API 없이 그린다).
  const nodes=pts.map(d=>({d, cx:px(d.ratio), cy:py(d.r.neff), r:pr(d.r.tot),
                           t:d.r.cat.name}));
  const tw=t=>{ let w=0; for(const ch of t) w += ch.charCodeAt(0)>0x1100 ? 10.5 : 6; return w; };
  const hitCircle=(bx,by,bw,bh,c)=>{
    const nx=Math.max(bx,Math.min(c.cx,bx+bw)), ny=Math.max(by,Math.min(c.cy,by+bh));
    return (nx-c.cx)**2 + (ny-c.cy)**2 < (c.r+2)**2; };
  const hitBox=(a,b)=> a.x < b.x+b.w && b.x < a.x+a.w && a.y < b.y+b.h && b.y < a.y+a.h;
  const placed=[];
  nodes.forEach(n=>{
    const w=tw(n.t), h=13;
    const cand=[
      {x:n.cx+n.r+6,      y:n.cy-h/2, anchor:'start'},
      {x:n.cx-n.r-6-w,    y:n.cy-h/2, anchor:'end'},
      {x:n.cx-w/2,        y:n.cy-n.r-6-h, anchor:'middle'},
      {x:n.cx-w/2,        y:n.cy+n.r+6, anchor:'middle'},
    ];
    let pick=cand.find(c=> c.x>=x0-40 && c.x+w<=x1+16 && c.y>=y0 && c.y+h<=y1
      && !nodes.some(o=> o!==n && hitCircle(c.x,c.y,w,h,o))
      && !placed.some(p=> hitBox({x:c.x,y:c.y,w,h}, p))) || cand[2];
    placed.push({x:pick.x, y:pick.y, w, h});
    n.lx = pick.anchor==='start'? pick.x : pick.anchor==='end'? pick.x+w : pick.x+w/2;
    n.ly = pick.y+h-3; n.anchor=pick.anchor;
  });
  nodes.forEach(n=>{
    const d=n.d;
    const tip=d.r.cat.name+' — 뉴스 비중 '+Math.round(d.ratio*100)+'%(이전=100), '
      + '실질 경쟁자 '+d.r.neff.toFixed(1)+'곳, 상위 3곳 '+Math.round(d.r.cr3*100)+'%';
    s+='<g><title>'+esc(tip)+'</title>'
      + '<circle class="dot" cx="'+n.cx.toFixed(1)+'" cy="'+n.cy.toFixed(1)+'" r="'+n.r.toFixed(1)
      + '" fill="'+col(d.lv)+'"/>'
      + '<circle class="hit" cx="'+n.cx.toFixed(1)+'" cy="'+n.cy.toFixed(1)+'" r="'+Math.max(n.r,13)+'"/>'
      + '<text class="ql" x="'+n.lx.toFixed(1)+'" y="'+n.ly.toFixed(1)+'" text-anchor="'
      + n.anchor+'">'+esc(n.t)+'</text></g>';
  });
  return '<div class="qwrap">'+s+'</svg></div>'
    + '<div class="qlegend"><span>권리 집중도</span>'
    + '<span class="s3"><i></i>소수 집중</span><span class="s2"><i></i>중간</span>'
    + '<span class="s1"><i></i>경쟁 분산</span><span>원 크기 = 그 분야 추정 공개 규모</span></div>';
}

function tradeSectionHTML(){
  const T=FEED.trade; if(!T) return '';
  const rows=tradeRows(); if(!rows.length) return '';
  const cmp=FEED.insights.comparable;
  const body=rows.map(d=>{
    const r=d.r, pct=Math.round(r.cr3*100);
    const badges=['상위 3곳 '+pct+'%'];
    if(d.lv) badges.push('실질 '+r.neff.toFixed(1)+'곳 / '+r.n+'곳');
    if(d.news && d.ratio!=null && cmp)
      badges.push('뉴스 비중 '+Math.round(d.ratio*100)+'%');
    const read=[T.conc[d.lv]||'', cmp? (T.news[d.dir]||'') : '',
                d.kr.length? T.kr : ''].filter(Boolean).join(' ');
    const holders=r.top.map(t=>concChip(t, r.tot)).join('');
    const krc=d.kr.length? '<div class="tk"><span class="cta">🇰🇷 국내 공개</span>'
      + d.kr.map(k=>'<span class="cta">'+esc(k)+'</span>').join('')+'</div>' : '';
    return '<div class="trow"><div class="th">'+r.cat.emoji+' '+esc(r.cat.name)
      + '<div class="tb">'+badges.map(b=>'<span>'+esc(b)+'</span>').join('')+'</div></div>'
      + '<div class="tk">'+holders+'</div>' + krc
      + '<p class="tr">'+esc(read)+'</p>'
      + (d.note? '<p class="tcaveat">※ '+esc(d.note)+'</p>' : '')
      + '</div>';
  }).join('');
  return '<div class="sec">🧭 분야별 거래 판단 참고</div>'
    + '<p class="gdesc">뉴스에서 차지하는 비중이 어느 쪽으로 움직였는지와, 그 분야 권리를 '
    + '몇 곳이 나눠 갖고 있는지를 겹쳐 봅니다. 원 크기는 그 분야의 추정 공개 규모입니다.'
    + (cmp? '' : ' (이전 기간 자료가 아직 부족해 뉴스 변화는 표시하지 않습니다.)')
    + '</p>'
    + quadChartHTML(rows) + body
    + '<p class="tcaveat" style="margin-top:12px">' + esc(T.unpaired) + '</p>'
    + '<p class="gnote">' + esc(T.note) + '</p>';
}

// 거래·지원: 수집 데이터와 무관한 안내 화면. 검색·필터가 필요 없어 홈과 같은
// 'homemode'(컨트롤 숨김)로 그린다.
function renderGuide(){
  const G = FEED.guide||[];
  $('#guide').innerHTML = tradeSectionHTML() + G.map(g=>
    '<div class="sec">'+esc(g.emoji||'')+' '+esc(g.name)+'</div>'
    + (g.desc? '<p class="gdesc">'+esc(g.desc)+'</p>' : '')
    + '<div class="glist">' + (g.items||[]).map(it=>
        '<a class="gcard" href="'+esc(safeUrl(it.url))+'" target="_blank" rel="noopener">'
        + '<div class="gname">'+esc(it.name)
        + (it.org? '<span class="gorg">'+esc(it.org)+'</span>' : '')
        + '<span class="garr" aria-hidden="true">↗</span></div>'
        + '<p class="gwhat">'+esc(it.what||'')+'</p></a>').join('')
    + '</div>').join('')
    + (FEED.guideNote? '<p class="gnote">'+esc(FEED.guideNote)+'</p>' : '');
}

function render(){
  const home = state.tab==='home', guide = state.tab==='guide';
  document.querySelector('.wrap').classList.toggle('homemode', home||guide);
  $('#home').hidden = !home;
  $('#guide').hidden = !guide;
  if(guide){ renderGuide(); updateViewToggle(); syncHash(); return; }
  if(home){ renderHome(); updateViewToggle(); syncHash(); return; }
  renderOverview();
  renderPeriodBar(); renderSource(); syncSearchPlaceholder();
  const list = filtered();
  const active = state.q || state.cats.size || state.countries.size || state.newonly
    || state.period!=='all' || state.source || state.savedOnly || state.unreadOnly;
  $('#resCount').innerHTML = '<b>'+list.length.toLocaleString()+'</b>건'
    + (active? ' <span style="opacity:.7">/ 전체 '+FEED[state.tab].items.length.toLocaleString()+'</span>' : '');
  // 특허 탭엔 '어느 기간 공개분인지'를 항상 명시(주별 버킷=수집 주와 혼동 방지)
  $('#scope').innerHTML = state.tab==='patents' ? patentScopeHTML() : '';
  $('#scope').hidden = state.tab!=='patents';
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
  const T=FEED.patents.totals||{};
  const byA={};
  list.forEach(it=>{ const nm=it.aName||'(미상)';
    const o=byA[nm]||(byA[nm]={cnt:0, flag:it.aFlag||'', region:it.aCountry||'', grid:{}});
    o.cnt++; o.grid[it.category]=(o.grid[it.category]||0)+1; if(!o.flag)o.flag=it.aFlag||''; });
  const CAP = FEED.patents.perApplicantLimit || 0;
  return Object.keys(byA).map(nm=>{
    const o=Object.assign({name:nm}, byA[nm]);
    // total: OPS 가 알려준 실제 전체 건수(있으면). cnt: 저장된 표본 수.
    o.exact = T[nm] != null;                 // 집계가 아직 안 돈 출원인은 false
    o.total = o.exact ? T[nm] : o.cnt;
    // 실제 건수를 모르는데 표본이 상한까지 찼으면 그 수는 '최소값'이다 →
    // 정확한 값처럼 보이지 않게 표본 표시(사선 막대 + '+')를 붙인다.
    o.sampled = o.exact ? (o.total > o.cnt) : (CAP > 0 && o.cnt >= CAP);
    return o;
  }).sort((a,b)=> b.total-a.total || a.name.localeCompare(b.name));
}
// 한 지역(부분집합)의 출원인×분야 표. opts.total → 합계 열
function matrixTableHTML(ranked, opts){
  const cats=FEED.patents.categories; opts=opts||{};
  if(!ranked.length) return '';
  let maxCell=1; ranked.forEach(r=>cats.forEach(c=>{const v=r.grid[c.key]||0; if(v>maxCell)maxCell=v;}));
  // 분야는 아이콘 대신 이름으로. 가운뎃점(·) 단위로 조각을 내어 조각 안에서는 절대 줄이 바뀌지
  // 않게 하고(.seg{nowrap}), 조각 사이 <wbr> 에서만 접히게 한다. 그냥 두면 좁은 폭에서 '·' 가
  // 혼자 한 줄을 차지한다. '·' 는 앞 조각에 붙여 '데이터센터·' / '무정전전원' 으로 접히게 한다.
  const catHead=c=>{ const parts=c.name.split('·');
    return parts.map((s,i)=>'<span class="seg">'+esc(s)+(i<parts.length-1?'·':'')+'</span>').join('<wbr>'); };
  const head='<tr><th class="cnr"></th>'+cats.map(c=>'<th class="cth" title="'+esc(c.name)+'">'
      +catHead(c)+'</th>').join('')
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
// 출원인 국적(지역)별로 나눈 매트릭스. opts.top 이 있으면 지역마다 상위 N 출원인만(홈 요약용).
function regionMatrixHTML(list, opts){
  opts = opts||{};
  const known=new Set(FEED.patents.countries.map(c=>c.code));
  const groups=FEED.patents.countries.slice();
  // 알 수 없는 지역(옛 데이터 등)은 버리지 않고 '기타'로 모아 KPI 합계와 어긋나지 않게 한다.
  if(list.some(it=>!known.has(it.aCountry))) groups.push({code:'', emoji:'🏳️', name:'기타'});
  const html=groups.map(rg=>{
    const sub=rg.code? list.filter(it=>it.aCountry===rg.code)
                     : list.filter(it=>!known.has(it.aCountry));
    if(!sub.length) return '';
    const all=_rankApplicants(sub);
    const ranked=opts.top? all.slice(0,opts.top) : all;
    const more=all.length-ranked.length;
    return '<div class="rgsec"><div class="rghead">'+rg.emoji+' <b>'+esc(rg.name)+'</b>'
      + ' <span class="rgn">'+sub.length+'건 · 출원인 '+all.length
      + (more>0? ' (상위 '+ranked.length+')':'') + '</span></div>'
      + matrixTableHTML(ranked, opts)+'</div>';
  }).filter(Boolean).join('');
  return html || '<p class="sub" style="margin:0">아직 수집된 특허가 없습니다.</p>';
}

// 랭킹 보기 방식: 'region'(출원인 국적별) | 'office'(공개 특허청=시장별) | 'all'(통합)
const RANK_MODES = ['region','office','all'];
let rankMode = RANK_MODES.includes(localStorage.getItem('pnp_rankMode'))
  ? localStorage.getItem('pnp_rankMode') : 'region';

function rankRowsHTML(rows, maxOverride){
  const maxA = maxOverride || (rows[0] ? rows[0].total : 1) || 1;
  return rows.map((r,i)=>{ const w=Math.max(2,r.total/maxA*100);
    const tip = r.exact ? (r.sampled? ' — 실제 '+r.total+'건 중 '+r.cnt+'건 저장':'')
      : (r.sampled? ' — 수집 상한까지 저장돼 실제는 '+r.cnt+'건 이상(정확 집계 대기)':'');
    return '<div class="row"><div class="nm" title="'+esc(r.name)+tip+'">'
      + '<span class="rk">'+(i+1)+'</span>'+(r.flag||'')+' '+esc(r.name)+'</div>'
      + '<div class="bar'+(r.sampled?' cap':'')+'" style="width:'+w.toFixed(1)+'%"></div>'
      + '<div class="val">'+r.total+(r.exact?'':(r.sampled?'+':''))+'</div></div>'; }).join('');
}

// 국적별 다출원 기업 — "미국에서 1등, 한국에서 1등" 을 한 화면에서 비교
function regionRankHTML(ranked){
  const out = FEED.patents.countries.map(rg=>{
    const sub = ranked.filter(r=>r.region===rg.code);
    if(!sub.length) return '';
    return '<div class="rgrank"><div class="rghead">'+rg.emoji+' <b>'+esc(rg.name)+'</b>'
      + ' <span class="rgn">'+sub.length+'곳</span></div>'
      + rankRowsHTML(sub.slice(0,5)) + '</div>';
  }).filter(Boolean).join('');
  return out || '<span class="unknown">—</span>';
}

// 공개 특허청(시장)별 다출원 기업 — "미국 시장에 누가 많이 내나"(국적 무관).
// officeCounts[출원인][특허청] = 실제 건수(OPS count 쿼리). 없으면 표본으로 근사.
function officeRankHTML(ranked){
  const OC = FEED.patents.officeCounts || {};
  const exact = Object.keys(OC).length > 0;
  const byName = {}; ranked.forEach(r=>byName[r.name]=r);
  const fallback = {};      // 표본 기반 근사(정확 집계가 없을 때)
  if(!exact){
    FEED.patents.items.forEach(it=>{
      const o=fallback[it.aName]||(fallback[it.aName]={});
      if(it.office) o[it.office]=(o[it.office]||0)+1; });
  }
  const src = exact? OC : fallback;
  const out = (FEED.patents.offices||[]).map(off=>{
    const rows = Object.keys(src).map(nm=>{
      const n = src[nm][off.code] || 0;
      const base = byName[nm] || {flag:'', name:nm};
      return n? {name:nm, flag:base.flag, total:n, cnt:n, sampled:false} : null;
    }).filter(Boolean).sort((a,b)=> b.total-a.total || a.name.localeCompare(b.name));
    if(!rows.length) return '';
    return '<div class="rgrank"><div class="rghead">'+off.emoji+' <b>'+esc(off.name)+'</b>'
      + ' <span class="rgn">'+rows.length+'곳</span></div>'
      + rankRowsHTML(rows.slice(0,5)) + '</div>';
  }).filter(Boolean).join('');
  return out || '<span class="unknown">—</span>';
}

// 해외 출원인이 한국에 공개한 특허 — 그들이 국내 시장에서 지킬 값어치가 있다고 본 기술.
// 국내 업계 입장에선 '누가 무엇을 들고 들어왔나'가 가장 실용적인 신호다.
function krEntryHTML(list){
  const rows=list.filter(it=>it.office==='KR' && it.aCountry!=='KR');
  if(!rows.length) return '';
  const by={};
  rows.forEach(it=>{ (by[it.aName]||(by[it.aName]={flag:it.aFlag,items:[]})).items.push(it); });
  const order=Object.entries(by).sort((a,b)=>b[1].items.length-a[1].items.length||a[0].localeCompare(b[0]));
  const cm={}; FEED.patents.categories.forEach(c=>cm[c.key]=c);
  const blocks=order.map(([nm,g])=>{
    const lis=g.items.slice(0,12).map(it=>{
      const c=cm[it.category];
      return '<li><a href="'+esc(safeUrl(it.url))+'" target="_blank" rel="noopener">'+esc(it.title)+'</a>'
        + (c?'<span class="kc">'+esc(c.name)+'</span>':'')
        + '<span class="kn mono">'+esc(it.number||'')+'</span></li>';
    }).join('');
    const more=g.items.length>12? '<li class="kmore">… 외 '+(g.items.length-12)+'건</li>':'';
    return '<div class="krow"><div class="kap">'+(g.flag||'')+' '+esc(nm)
      + '<span class="kcnt">'+g.items.length+'건</span></div><ul class="klist">'+lis+more+'</ul></div>';
  }).join('');
  return '<div class="panel wide krpanel"><h3>🇰🇷 해외 출원인의 국내 공개</h3>'
    + '<p class="sub">해외 출원인이 <b>한국에 공개</b>한 특허입니다. 여러 관할 구역 가운데 한국이 '
    + '포함됐다는 점에서, 해당 기술의 국내 권리화를 함께 고려한 것으로 볼 수 있습니다. '
    + '분야별 동향을 살피는 데 참고가 됩니다. 제목을 누르면 원문으로 이동. '
    + '<br>※ 매주 해외 출원인별로 국내 공개분을 따로 조회해 모읍니다(출원인당 최대 '
    + (FEED.patents.krLimit||15)+'건). 쿼터에 걸리면 다음 주에 이어서 채웁니다.</p>'
    + '<div class="krwrap">'+blocks+'</div></div>';
}

// ── 분야별 경쟁 구도 ───────────────────────────────────────────────
// 표본을 그대로 세면 안 된다. 수집은 출원인당 상한(PER_APPLICANT_LIMIT)이 있어
// 큰 기업일수록 잘려 나가고, 잘린 곳들이 표본의 대부분을 차지한다. 실측하면
// LG에너지솔루션은 실제 2,264건인데 표본엔 52건뿐이었다 — 이 상태로 세면
// 재생에너지 분야가 '분산'(31%)으로 나오지만, 규모를 되돌리면 '소수 집중'(74%)이다.
//
// 그래서 두 값을 나눠 쓴다:
//   분야 구성비 → 표본에서 (그 출원인이 어느 분야에 내는지의 비율은 표본으로도 보인다)
//   규모        → OPS 가 알려준 실제 총계 (상한과 무관하게 정확하다)
//   추정 건수 = 실제 총계 × (그 분야 표본 / 그 출원인 표본 전체)
//
// 지표는 둘이다.
//   CR3   : 상위 3곳의 몫. 한눈에 읽히지만 4위 이하의 모양을 못 본다.
//   실질 N: 1/HHI(지분 제곱합의 역수). 규모 차이를 반영한 '실질 경쟁자 수'로,
//           출원인이 35곳이어도 셋이 다 가져가면 4곳 수준으로 나온다.
const CONC_MIN = 4;          // 등장 출원인이 이보다 적으면 CR3 가 항상 100% 라 무의미
function concentration(list){
  const ranked = _rankApplicants(list).filter(r=>r.name!=='(미상)');
  return (FEED.patents.categories||[]).map(c=>{
    const sh=[];
    ranked.forEach(r=>{ const g=r.grid[c.key]||0;
      if(!g || !r.cnt) return;
      sh.push({name:r.name, flag:r.flag, v:r.total*g/r.cnt, raw:g}); });
    if(!sh.length) return null;
    sh.sort((a,b)=> b.v-a.v || a.name.localeCompare(b.name));
    const tot = sh.reduce((s,x)=>s+x.v,0);
    // 보정 전(표본 그대로) 값도 같이 낸다 — 툴팁에서 보정 폭을 밝히기 위해서다.
    const rawSorted = sh.map(x=>x.raw).sort((a,b)=>b-a);
    const rawTot = rawSorted.reduce((s,v)=>s+v,0);
    return {cat:c, n:sh.length, tot,
      cr3: tot? sh.slice(0,3).reduce((s,x)=>s+x.v,0)/tot : 0,
      neff: tot? 1/sh.reduce((s,x)=>s+(x.v/tot)*(x.v/tot),0) : 0,
      rawCr3: rawTot? rawSorted.slice(0,3).reduce((s,v)=>s+v,0)/rawTot : 0,
      top: sh.slice(0,3)};
  }).filter(Boolean).sort((a,b)=> b.cr3-a.cr3);
}

// 상위 보유자 칩. 수치는 건수가 아니라 분야 내 지분(%)이다 — 추정치를 건수로 쓰면
// 없는 정밀도를 있는 것처럼 보이게 한다. 총계를 깎아 쓴 출원인은 * 를 달고 툴팁에
// 근거를 적는다(수치를 조용히 고쳐 두지 않는다).
function concChip(t, tot){
  const a=(FEED.patents.totalsAdjusted||{})[t.name];
  const tip = a ? (t.name+' — 검색어가 넓어 자회사 문서까지 조회됐습니다. 표본 '+a.of
    +'건 중 실제로 이 회사 것이었던 '+a.kept+'건의 비율로, 총계를 '+a.raw
    +'건에서 깎아 썼습니다.') : '';
  return '<span class="cta"'+(tip? ' title="'+esc(tip)+'"' : '')+'>'
    + (t.flag||'') + ' ' + esc(t.name) + (a? '<span class="ctn">*</span>' : '')
    + '<span class="ctn">'+Math.round(t.v/tot*100)+'%</span></span>';
}

function concRowsHTML(rows){
  return rows.map(r=>{
    let metric;
    if(r.n < CONC_MIN){
      metric = '<span class="conc na" title="이 분야에 등장한 출원인이 '+r.n
        + '곳뿐이라 집중도를 계산하지 않습니다(3곳 이하면 상위 3곳 몫이 항상 100%)">'
        + '출원인 '+r.n+'곳</span>';
    } else {
      const pct = Math.round(r.cr3*100), ne = r.neff;
      const lv = ne<5 ? ['소수 집중','hi'] : ne<8 ? ['중간','mid'] : ['경쟁 분산','lo'];
      const tip = '상위 3곳이 이 분야의 '+pct+'%를 차지합니다. 등장 출원인은 '+r.n
        + '곳이지만 규모 차이를 반영하면 실질 경쟁자는 '+ne.toFixed(1)+'곳 수준입니다. '
        + '표본을 그대로 세면 '+Math.round(r.rawCr3*100)+'%지만, 출원인당 수집 상한 때문에 '
        + '큰 기업이 잘려 있어 실제 공개 총계로 규모를 되돌려 계산한 값입니다.';
      metric = '<span class="conc '+lv[1]+'" title="'+esc(tip)+'">'
        + '<span class="cbar"><i style="width:'+pct+'%"></i></span>'
        + '<span class="cpct mono">'+pct+'%</span><span class="clv">'+lv[0]+'</span>'
        + '<span class="cn">실질 '+ne.toFixed(1)+'곳 / '+r.n+'곳</span></span>';
    }
    // 칩의 수치는 건수가 아니라 분야 내 지분(%)이다 — 추정치라 건수로 쓰면
    // 없는 정밀도를 있는 것처럼 보이게 한다.
    const chips = r.top.map(t=>concChip(t, r.tot)).join('');
    return '<div class="crow"><div class="clab">'+r.cat.emoji+' '+esc(r.cat.name)+metric+'</div>'
      + '<div class="ctops">'+(chips||'<span class="unknown">—</span>')+'</div></div>';
  }).join('');
}

function renderStats(list){
  if(!list.length) return '<div class="empty">조건에 맞는 특허가 없습니다.</div>';
  const cats=FEED.patents.categories, regions=FEED.patents.countries;
  const ranked=_rankApplicants(list);
  const uniq=ranked.length, topA=ranked[0];
  const regCnt={}; ranked.forEach(r=>{ regCnt[r.region]=(regCnt[r.region]||0)+1; });
  const regChips=regions.map(rg=>regCnt[rg.code]?(rg.emoji+regCnt[rg.code]):'').filter(Boolean).join(' ');
  const catLeadRows = concRowsHTML(concentration(list));
  // 랭킹(전 지역 통합). 수집 상한에 걸린 곳은 실제 건수가 그 이상이라 '50+' 로 표기하고
  // 막대도 구분한다 — 상한 동점끼리 순위를 매기면 정렬 우연을 실력처럼 보여주게 된다.
  const nSampled=ranked.filter(r=>r.sampled).length;
  const leadRows = rankMode==='region' ? regionRankHTML(ranked)
    : rankMode==='office' ? officeRankHTML(ranked)
    : rankRowsHTML(ranked.slice(0,15));
  const exactOffice = Object.keys(FEED.patents.officeCounts||{}).length>0;
  const rankSub = rankMode==='region'
      ? '<b>출원인 국적별</b> — 그 나라 기업 중 다출원 순서(지역별 상위 5) · 실제 공개 건수 기준.'
        + (nSampled? ' 사선 막대 '+nSampled+'곳은 목록에 표본만 저장돼 있습니다.' : '')
    : rankMode==='office'
      ? '<b>공개 특허청(시장)별</b> — 그 특허청에 많이 공개한 기업(국적 무관, 상위 5). '
        + (exactOffice? '실제 공개 건수 기준.' : '표본 기반 근사치(다음 수집부터 정확).')
        + ' 같은 발명이 여러 나라에 공개되므로 특허청별 합계는 출원인 총계를 넘을 수 있습니다.'
      : '전 지역 통합 상위 15 · 실제 공개 건수 기준(수집 상한과 무관).';

  return '<div class="stats">'
    + '<div class="panel wide"><div class="statkpi">'
      + '<div><div class="k">분석 출원인</div><div class="v mono">'+uniq
        + ' <span style="font-size:12px;color:var(--muted)">'+regChips+'</span></div></div>'
      + '<div><div class="k">수집 특허(표본)</div><div class="v mono">'+list.length.toLocaleString()+'</div></div>'
      + '<div><div class="k">최다 출원인</div><div class="v">'+(topA.flag||'')+' '+esc(topA.name)
        + ' <span style="font-size:14px;color:var(--muted)" class="mono">'+topA.total+'건</span></div></div>'
      + '</div></div>'
    + '<div class="panel wide"><h3>🧩 출원인 × 분야 매트릭스 <span style="color:var(--muted);font-weight:600;font-size:12px">출원인 국적별</span></h3>'
      + '<p class="sub">출원인을 국적(🇺🇸미국·🇰🇷한국·🇨🇳중국·🇯🇵일본·🇪🇺유럽)으로 묶어, 각 기업이 <b>어느 분야에</b> 최근 특허를 냈는지 봅니다(표본 내 건수, 진할수록 많음). 칸을 누르면 해당 출원인·분야 특허로 이동. '
      + '※ 특허가 <b>공개된 특허청</b>은 이와 별개이며(한 기업이 여러 나라에 출원), 각 특허 카드에 표시됩니다.</p>'
      + regionMatrixHTML(list, {total:true}) + '</div>'
    + krEntryHTML(list)
    + '<div class="panel"><h3>🧭 분야별 경쟁 구도</h3>'
      + '<p class="sub">각 분야를 <b>몇 곳이 나눠 갖고 있는지</b>입니다. 막대는 <b>상위 3곳의 몫</b>, '
      + '‘실질 N곳’은 규모 차이를 반영한 경쟁자 수입니다(출원인이 35곳이어도 셋이 대부분을 가져가면 4곳 수준으로 나옵니다). '
      + '칩의 %는 그 분야 안에서의 지분입니다. 집중된 분야일수록 회피설계·라이선스 검토가 먼저 필요합니다.<br>'
      + '수집은 출원인마다 상한이 있어 큰 기업이 잘려 있습니다 → 분야 구성비는 표본에서, 규모는 실제 공개 총계에서 가져와 되돌린 <b>추정치</b>입니다. '
      + '<b>※ 시장 점유율이 아닙니다</b> — 추적 중인 주요 출원인 '+(FEED.patents.applicants||0)+'곳 안에서의 분포입니다.</p>'
      + '<div class="catlead">'+catLeadRows+'</div></div>'
    + '<div class="panel"><h3>🏆 출원인 랭킹'
      + '<span class="rankseg">'
      + '<button data-rank="region" aria-pressed="'+(rankMode==='region')+'" title="그 나라 기업 중 다출원">출원인 국적별</button>'
      + '<button data-rank="office" aria-pressed="'+(rankMode==='office')+'" title="그 특허청에 많이 낸 기업(국적 무관)">공개국별</button>'
      + '<button data-rank="all" aria-pressed="'+(rankMode==='all')+'">전체</button></span></h3>'
      + '<p class="sub">'+rankSub+'</p>'
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
  const t=p.get('tab'); if(t==='news'||t==='patents'||t==='home'||t==='guide') state.tab=t;
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
  resetFilters();
  state.q=opts.q||''; if(opts.cat) state.cats.add(opts.cat);
  const q=$('#q'); if(q) q.value=state.q;
  syncTabsUI(); updateViewToggle(); renderChips(); render();
  window.scrollTo({top:0, behavior:'smooth'});
}

function resetFilters(){
  state.q=''; state.cats.clear(); state.countries.clear();
  state.period='all'; state.source=''; state.limit=PAGE;
  state.newonly=false; state.savedOnly=false; state.unreadOnly=false;
  const q=$('#q'); if(q) q.value='';
  ['newonly','savedonly','unreadonly'].forEach(id=>{
    const b=$('#'+id); if(b) b.setAttribute('aria-pressed','false'); });
}

function setTab(t){
  if(state.tab===t) return;
  // 탭을 바꾸면 필터를 모두 초기화한다. 특히 홈은 검색창이 숨겨져 있어
  // 남아있는 검색어/토글을 사용자가 인지·해제할 방법이 없다.
  state.tab=t; state.view='list';
  resetFilters();
  syncTabsUI();
  updateViewToggle(); renderChips(); render();
}

function wire(){
  document.querySelectorAll('.tabs button').forEach(b=> b.onclick=()=>setTab(b.dataset.tab));
  // 상단 제목 → 홈. 이미 홈이면 setTab 이 그냥 빠져나오므로 맨 위로 올려주기만 한다.
  $('#brand').onclick = ()=>{ setTab('home'); window.scrollTo({top:0, behavior:'smooth'}); };
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
      localStorage.setItem('pnp_briefClosed', briefCollapsed?'1':'0');
      // 홈 전체가 아니라 브리핑 카드만 교체 → 펼쳐둔 '지난 브리핑' 타임라인이 유지된다.
      const cur=$('#home').querySelector('.brief');
      if(cur){ const tmp=document.createElement('div'); tmp.innerHTML=briefHTML();
        const next=tmp.firstElementChild; if(next) cur.replaceWith(next); }
      else renderHome();
      return; }
    // 지난 브리핑 타임라인 펼치기
    const tl=e.target.closest('.tl'); if(tl && !e.target.closest('[data-go]')){ tl.classList.toggle('open'); return; }
    // 키워드 → 뉴스 탭에서 검색
    const kw=e.target.closest('[data-kw]'); if(kw){ gotoTab('news', {q:kw.getAttribute('data-kw')}); return; }
    // 특허 통계 전체 보기
    // data-go="patents-stats" → 통계 뷰, "patents" → 목록 뷰(브리핑 전문이 그 위에 있다)
    const go=e.target.closest('[data-go]');
    if(go){ gotoTab('patents', go.getAttribute('data-go')==='patents-stats'?{view:'stats'}:{}); return; }
    // 매트릭스 칸 → 특허 탭에서 그 출원인·분야
    const mc=e.target.closest('.pmx td.has[data-ap]');
    if(mc){ gotoTab('patents', {q:mc.getAttribute('data-ap'), cat:mc.getAttribute('data-cat')}); return; }
    // 이슈 흐름 행 → 뉴스 탭 카테고리 필터
    const row=e.target.closest('.trend [data-cat]');
    if(row){ gotoTab('news', {cat:row.getAttribute('data-cat')}); return; }
  };
  $('#results').addEventListener('click', e=>{
    // 랭킹 보기 토글(국적별 / 전체)
    const rb=e.target.closest('[data-rank]');
    if(rb){ rankMode=rb.getAttribute('data-rank');
      localStorage.setItem('pnp_rankMode', rankMode); render(); return; }
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

$('#foot').innerHTML = '뉴스: Google 뉴스 RSS(매일 수집) · 특허: EPO OPS 공식 API에서 주요 출원인 '
  + (FEED.patents.applicants||0) + '곳 × 전력 CPC로 매주 수집(1회 조회 범위 = 최근 '
  + (FEED.patents.lookbackDays||90) + '일 공개분, 새로 공개된 것만 누적). '
  + '제목·요약·링크는 원문으로 연결됩니다. 본 사이트는 이슈 아카이브용이며 특정 투자·정책 판단을 권유하지 않습니다.'
  + '<br>최종 갱신 <b class="mono">'+esc(FEED.generated)+'</b> · 뉴스 '+FEED.news.items.length
  + '건 · 특허 '+FEED.patents.items.length+'건'
  // 기관 홈페이지로 되걸어 공식 서비스임을 확인할 수 있게 한다(주소가 기관 도메인이 아니다).
  + (FEED.org? '<br>운영 <b>'+esc(FEED.org)+'</b>'
      + (FEED.orgUrl? ' · <a href="'+esc(safeUrl(FEED.orgUrl))+'" target="_blank" rel="noopener">'
          + esc(FEED.club || '기관 홈페이지') + '</a>' : '')
      : '');

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
