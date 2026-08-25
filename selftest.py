"""수집기 스모크 테스트 — 네트워크·키 없이 '라이브 경로'를 실제로 실행해 본다.

왜 필요한가: MOCK 빌드는 _mock_collect 만 타고, 매일 도는 뉴스 실행은
collect_offices 만 탄다 → OPS 를 쓰는 _live_collect 는 주 1회 특허 워크플로에서만
실행돼, 거기 있는 오류(예: 미정의 변수)가 월요일에야 드러난다. py_compile 로는
NameError 를 못 잡으므로, OPS 응답을 흉내 내는 스텁을 끼워 두 경로를 다 돌려본다.

사용: python selftest.py   (의존성 없음. 실패하면 exit 1)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

import patent_config as cfg
import patent_source as ps

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ── OPS 응답 스텁 (값이 {"$": ...} 로 감싸인 실제 구조를 그대로 흉내) ──
def _doc(number: str, date: str, cpc: str) -> dict:
    return {"exchange-document": {"bibliographic-data": {
        "publication-reference": {"document-id": [{
            "@document-id-type": "docdb",
            "country": {"$": number[:2]}, "doc-number": {"$": number[2:-2]},
            "kind": {"$": number[-2:]}, "date": {"$": date}}]},
        "invention-title": [{"@lang": "en", "$": "Stub power apparatus"}],
        "parties": {"applicants": {"applicant": [
            {"@data-format": "original",
             "applicant-name": {"name": {"$": "STUB CO LTD"}}}]}},
        "patent-classifications": {"patent-classification": [{
            "section": {"$": cpc[0]}, "class": {"$": cpc[1:3]},
            "subclass": {"$": cpc[3]}, "main-group": {"$": cpc[4:]}}]},
    }}}


class Stub:
    """_search 를 대신한다. 호출마다 다른 공개번호를 줘 중복 제거에 다 걸리지 않게."""

    def __init__(self, total: int = 7, per_call: int = 2):
        self.total, self.per_call, self.calls = total, per_call, 0

    def __call__(self, token, cql, start, end, timeout=None):
        self.calls += 1
        check_range = start <= end
        if not check_range:                       # '51-50' 같은 역전 범위 방지 확인
            raise AssertionError(f"잘못된 OPS 범위: {start}-{end}")
        docs = [_doc(f"US{9000000 + self.calls * 10 + i}A1", "20260701", "H02J3")
                for i in range(self.per_call)]
        return ({"ops:world-patent-data": {"ops:biblio-search": {
            "@total-result-count": str(self.total),
            "ops:search-result": {"exchange-documents": docs}}}}, self.total)


def _kipris_page(rows: list[tuple[int, str, str]], total: int) -> str:
    body = "".join(
        f"<item><applicantName>{who}</applicantName>"
        f"<applicationDate>20240701</applicationDate>"
        f"<applicationNumber>10202400{i:05d}</applicationNumber>"
        f"<astrtCont>초록 {i}</astrtCont><inventionTitle>제목 {i}</inventionTitle>"
        f"<ipcNumber>{ipc}</ipcNumber><openDate>20260115</openDate>"
        f"<openNumber>102026{i:07d}</openNumber>"
        f"<registerStatus>공개</registerStatus></item>"
        for i, who, ipc in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?><response><header>'
            "<resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg>"
            f"</header><body><items>{body}</items>"
            f"<count><totalCount>{total}</totalCount></count></body></response>")


def _favicon_checks(sr) -> None:
    """탭 아이콘. 여기서 틀리면 오류 없이 조용히 아이콘만 사라진다.

    특히 data URI 는 HTML 속성 안에 그대로 들어가므로, 따옴표나 꺾쇠가 살아
    있으면 속성이 그 자리에서 끊긴다 — 페이지는 멀쩡히 뜨고 아이콘만 안 나온다.
    """
    import struct
    import tempfile
    from pathlib import Path

    import favicon as fv

    svg = fv.svg()
    check(svg.startswith("<svg") and svg.rstrip().endswith("</svg>"),
          "SVG 가 온전히 닫힌다")
    check('width="32"' in svg and 'viewBox="0 0 32 32"' in svg,
          "SVG 에 고유 크기와 viewBox 가 다 있다")
    check(f"#{fv.BG[0]:02X}{fv.BG[1]:02X}{fv.BG[2]:02X}" in svg,
          "타일 색이 사이트 강조색(--accent)과 같다")
    import re as _re2
    pts = _re2.search(r'points="([^"]+)"', svg)
    pairs = pts.group(1).split() if pts else []
    check(len(pairs) == len(fv.BOLT)
          and all(len(p.split(",")) == 2 for p in pairs),
          f"번개 꼭짓점이 {len(fv.BOLT)}개 다 들어간다 (읽은 값 {len(pairs)}개)")
    # 벡터와 래스터가 같은 좌표를 쓰는지. 갈라지면 탭과 바로가기의 모양이 달라진다.
    fit = fv._fit(32)
    check(all(abs(float(p.split(",")[0]) - fit[i][0]) < 0.01
              and abs(float(p.split(",")[1]) - fit[i][1]) < 0.01
              for i, p in enumerate(pairs)),
          "SVG 좌표가 래스터와 같은 _fit() 에서 나온다")

    uri = fv.data_uri()
    check(uri.startswith("data:image/svg+xml,"), "data URI 형식이 맞다")
    bad = [c for c in '"<>#' if c in uri]
    check(not bad,
          f"data URI 에 속성을 깨뜨리는 문자가 없다 (발견: {bad or '없음'})")

    # PNG 서명과 실제 크기
    for size in (16, 32, 180):
        data = fv.png(size)
        w, h = struct.unpack(">II", data[16:24])
        check(data[:8] == b"\x89PNG\r\n\x1a\n" and (w, h) == (size, size),
              f"PNG {size}px 가 유효하다 ({w}x{h})")

    # ICO 헤더: reserved=0, type=1, 그리고 담은 장수
    blob = fv.ico()
    res, typ, cnt = struct.unpack("<HHH", blob[:6])
    check((res, typ) == (0, 1) and cnt == 2,
          f"ICO 헤더가 맞다 (type={typ}, 장수={cnt})")
    # 각 항목이 가리키는 자리에 진짜 PNG 가 있는지 — 오프셋을 잘못 쌓으면
    # 파일 크기는 그럴듯한데 브라우저가 못 읽는다.
    okpng = True
    for i in range(cnt):
        off, = struct.unpack("<I", blob[6 + 16 * i + 12: 6 + 16 * i + 16])
        n, = struct.unpack("<I", blob[6 + 16 * i + 8: 6 + 16 * i + 12])
        if blob[off:off + 8] != b"\x89PNG\r\n\x1a\n" or off + n > len(blob):
            okpng = False
    check(okpng, "ICO 안의 각 항목이 실제 PNG 를 가리킨다")

    with tempfile.TemporaryDirectory() as td:
        fv.write(Path(td))
        made = sorted(p.name for p in Path(td).iterdir())
        check(made == ["apple-touch-icon.png", "favicon.ico", "favicon.svg"],
              f"아이콘 파일 세 개를 쓴다 ({', '.join(made)})")

    # 페이지가 실제로 그걸 가리키는지. favicon.ico 는 링크가 없어도 브라우저가
    # 자동으로 찾는 자리라, 파일만 있으면 404 는 사라진다.
    page = sr._PAGE
    check('rel="icon"' in page and "__FAVICON__" in page,
          "head 에 기본 아이콘 링크가 있다")
    check('href="favicon.ico"' in page, "head 에 ico 대비책이 있다")
    check('rel="apple-touch-icon"' in page, "head 에 iOS 바로가기 아이콘이 있다")


def _lazy_checks(sr) -> None:
    """목록 분할과, 분할해도 집계가 흔들리지 않는지.

    이 기능의 위험은 '느려진다'가 아니라 **숫자가 조용히 작아진다**는 것이다.
    집계를 자르기 전 전체로 계산하는 순서가 한 번만 어긋나도 화면의 모든 수가
    부분집합 기준이 되는데, 그건 눈으로 봐서는 알 수 없다.
    """
    import json
    import tempfile
    from pathlib import Path

    items = [{"title": f"제목 {i}", "url": f"u{i}", "number": f"N{i}",
              "pub_date": f"2026-08-{(i % 28) + 1:02d}"} for i in range(50)]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        sec = {"items": list(items), "totals": {"한국전력공사": 999}}
        rest = sr._split_items(sec, 10, "patents", d)

        check(rest == 40, f"나머지를 갈라낸다 (뒤 {rest}건)")
        check(len(sec["items"]) == 10, f"인라인은 상한만큼만 ({len(sec['items'])}건)")
        check(sec["total"] == 50, f"total 은 자르기 전 전체 ({sec['total']})")
        check(sec["totals"] == {"한국전력공사": 999},
              "집계는 분할이 건드리지 않는다")

        f = d / sr.FEED_SUBDIR / "patents-rest.json"
        check(f.exists(), "나머지 파일이 만들어진다")
        tail = json.loads(f.read_text(encoding="utf-8"))
        check(len(tail) == 40, f"나머지 파일에 뒤가 다 있다 ({len(tail)}건)")

        # 합치면 원본과 정확히 같아야 한다 — 겹치거나 빠지면 목록이 조용히 틀어진다.
        merged = {x["number"] for x in sec["items"]} | {x["number"] for x in tail}
        check(merged == {x["number"] for x in items},
              f"인라인 + 나머지 = 원본 (합 {len(merged)}건, 중복·누락 없음)")
        check(not ({x["number"] for x in sec["items"]}
                   & {x["number"] for x in tail}),
              "인라인과 나머지가 겹치지 않는다")

        # 최신순으로 갈라야 첫 화면이 최근 것으로 찬다.
        newest = sorted((x["pub_date"] for x in items), reverse=True)[:10]
        check(sorted((x["pub_date"] for x in sec["items"]), reverse=True) == newest,
              "인라인에 남는 것은 가장 최근 건들이다")

        # 상한보다 적으면 그냥 다 인라인 — 쓸데없는 요청을 만들지 않는다.
        small = {"items": items[:5]}
        check(sr._split_items(small, 10, "news", d) == 0
              and "rest" not in small and small["total"] == 5,
              "상한보다 적으면 나누지 않는다")

        # 탈출구: 통째로 인라인(로컬 검증·오프라인 배포).
        saved = sr.INLINE_ALL
        try:
            sr.INLINE_ALL = True
            whole = {"items": list(items)}
            check(sr._split_items(whole, 10, "patents", d) == 0
                  and len(whole["items"]) == 50 and "rest" not in whole,
                  "NEWS_INLINE_ALL 이면 나누지 않는다")
        finally:
            sr.INLINE_ALL = saved

    js = sr._JS
    check("hydrate()" in js, "첫 그림 뒤 나머지를 받는 호출이 있다")
    check("FEED[t].total" in js or "FEED[t] && FEED[t].total" in js,
          "건수는 total 로 읽는다 (items.length 는 로딩 중 작다)")
    check("_shareCache = null" in js,
          "다 받은 뒤 항목 기반 캐시를 버린다 (안 버리면 최근분으로 계산한 값이 남는다)")
    # 국적을 모르는 출원인을 화면에서 빼기만 하면 '🇺🇸8곳' 으로 보여 미국 기업이
    # 여덟 곳뿐인 것으로 읽힌다(실측: 5,403곳 중 국적을 아는 곳이 1,186곳뿐).
    # 모르는 것은 모른다고 두되, 모른다는 사실도 화면에 남아야 한다.
    # 수집 축이 OPS(출원인 목록) → KIPRIS(분야+기간 전수) 로 바뀌었다. 화면 문구가
    # 옛 방식 그대로면 읽는 사람은 아직 65곳만 보는 줄 안다 — 기관 서비스에서
    # 사실과 다른 설명은 그 자체가 결함이다.
    check("EPO OPS" not in js,
          "화면 문구에 EPO OPS 가 남아 있지 않다 (지금 쓰는 것은 KIPRISplus)")
    check("KIPRISplus" in js, "푸터가 실제 출처(KIPRISplus)를 밝힌다")
    # 국기 — 윈도우에는 국기 이모지 글꼴이 없어 지역표시자 두 글자가 그대로 찍힌다
    # (실측: 화면이 'US8KR1143CN11JP11EU13' 이 됐다). 표시 직전에 우리가 그린
    # 것으로 바꾸므로, 국기가 나오는 자리는 전부 flg() 를 거쳐야 한다.
    check("function flg(" in js, "국기를 그려 주는 헬퍼가 있다")
    check("0x1F1E6" in js,
          "지역표시자에서 나라 코드를 되돌린다 (표를 따로 두지 않는다)")
    for sym in ("f-KR", "f-US", "f-CN", "f-JP", "f-EU"):
        check('id="' + sym + '"' in sr._PAGE, f"{sym} 국기를 그려 둔다")
    # 이모지가 flg() 를 거치지 않고 그대로 붙는 자리가 남아 있으면 안 된다.
    import re as _re3
    raw = _re3.findall(r"\+\s*\(?(?:it|p|r|rg|off|top|topA)\.(?:aFlag|flag|emoji)\s*\|\|", js)
    check(not raw, f"국기를 flg() 없이 그대로 붙이는 자리가 없다 (발견 {len(raw)}곳)")
    # 반대쪽 실수도 있다 — flg() 가 돌려준 SVG 를 문자열에 담아 두었다가 나중에
    # esc() 로 흘려보내면 '<svg class="fl" …>' 가 글자 그대로 화면에 찍힌다
    # (실측: 거래·지원 '국내 권리 N곳' 칩과 판정 문장 양쪽에서 났다).
    # 그리는 자리에서 바로 부르면 안전하다. 위험한 것은 '데이터에 담아 두는' 자리다
    # — 담아 둔 문자열은 나중에 esc() 를 지나며 마크업이 글자로 찍힌다. 거래·지원의
    # '국내 권리 N곳' 이 실제로 그랬다. 그 자리를 이름·국기 따로 담게 고쳤고,
    # 되돌아가면 여기서 걸린다.
    check("m.set(it.aName, it.aFlag)" in js,
          "국내 권리 목록은 이름과 국기를 따로 담는다")
    check("s.add(flg(" not in js and "add(flg(" not in js,
          "국내 권리 목록에 flg() 결과(SVG)를 담아 두지 않는다")
    check("EPO" not in js and "전력 CPC" not in js,
          "홈 타일 설명에도 EPO·전력 CPC 라는 옛 근거가 남아 있지 않다")
    check("전 세계 공개" not in js,
          "'전 세계 공개' 라고 하지 않는다 (국내 + 미국·유럽·일본·중국 넷이다)")
    check("칸의 수는 표본 건수" not in js and "출원인마다 수집 상한이 있어" not in js,
          "매트릭스 안내가 '칸은 표본' 이라고 하지 않는다 (지금은 전수라 실제 건수다)")
    check("규모를 실제 총계로 되돌린" not in js,
          "'총계로 되돌린' 이라는 OPS 시절 보정 이야기가 남아 있지 않다")
    check("곳을 매주 조회해" not in js,
          "'주요 출원인 N곳을 조회' 라는 옛 설명이 남아 있지 않다")
    check("안에서의 분포입니다" not in js
          or "추적 중인 주요 출원인" not in js,
          "'추적 중인 N곳 안에서의 분포' 라는 옛 단서가 남아 있지 않다")

    check("const unknown = uniq - known" in js,
          "국적 미상 출원인 수를 센다")
    check("국적미상 " in js, "KPI 에 '국적미상 N' 을 함께 보인다")
    check("국적을 알 수 없는" in js,
          "국적별 랭킹이 '빠진 곳이 있다'고 밝힌다")
    check("uniq.toLocaleString()" in js,
          "출원인 수도 천 단위로 끊는다 (옆의 미상 수와 표기가 어긋나지 않게)")

    for name, guard in (("공급자 표", "FULL ? supplierHTML"),
                        ("경쟁 구도", "if(!FULL) return '<div class=\"sec\" id=\"sec-analysis\">"),
                        ("통계 뷰", "FULL ? renderStats")):
        check(guard in js, f"{name}는 다 받기 전에는 그리지 않는다")


def _quad_checks(sr) -> None:
    """분야 지도(quadChartHTML)를 실제 값 분포로 그려 보고 눈으로 볼 것을 대신 센다.

    왜 필요한가: 축 범위를 코드에 고정해 두었는데, 수집 방식이 바뀌면서 값의
    자릿수가 통째로 달라졌다. y 축은 실질 경쟁자 2~12곳 기준이었는데 실측이
    10~239곳이 되어 여덟 중 일곱이 바닥에 눌어붙었고, 등급 경계(5곳/8곳)도 같은
    시절 값이라 여덟 분야가 전부 한 등급으로 나와 색이 아무것도 구분하지 못했다.
    둘 다 '그려 놓고 보면' 바로 보이지만 코드만 읽으면 안 보인다 → 실제 분포를
    넣고 (ⓐ 점이 축에 퍼지는가 ⓑ 등급이 갈리는가 ⓒ 원이 판 안에 있는가
    ⓓ 이름표가 겹치지 않는가) 를 센다.
    """
    import json
    import re as _re
    import shutil
    import subprocess
    import tempfile

    js = sr._JS
    check("constNEF=[8,256]" in js.replace(" ", ""),
          "y 축 고정 범위가 실측 분포(10~239곳)를 담는다")
    if not shutil.which("node"):
        check(True, "(node 없음 — 분야 지도 실행 검사는 건너뛴다)")
        return

    cs, ce = js.find("const CONC_MID"), js.find("function concentration")
    qs, qe = js.find("const QW=680"), js.find("const STAOWN_HEAD")
    check(cs >= 0 < ce - cs and qs >= 0 < qe - qs, "분야 지도 블록을 떼어낼 수 있다")
    if not (0 <= cs < ce and 0 <= qs < qe):
        return

    # 2026-08-25 실측값(전수 수집 뒤). 축·등급이 이 분포를 못 담으면 실패한다.
    rows = [("계량·스마트그리드", 10.0, 216, 0.37, 1.00),
            ("원전·SMR", 42.0, 382, 0.20, 1.04),
            ("재생에너지·저장", 43.0, 8013, 0.23, 1.00),
            ("전력수급·수요관리", 43.2, 2217, 0.18, 0.86),
            ("전력설비·기기", 52.8, 461, 0.18, 1.12),
            ("송·변전·전력망", 62.9, 1679, 0.18, 1.36),
            ("데이터센터·무정전전원", 106.6, 249, 0.11, 0.95),
            ("전력반도체·전력변환", 239.3, 1658, 0.06, 0.23)]
    # 이름표가 몰리는 경우. 실질 경쟁자 수가 거의 같은 분야가 나란히 서면 위·아래
    # 자리를 서로 뺏는다 — 실제로 재생에너지(43.0곳)와 원전(42.0곳)이 그랬다.
    # 네 곳까지는 오른쪽·왼쪽·위·아래 네 자리로 덮이므로 여섯 곳으로 민다.
    crowd = [(f"긴이름분야{i}", 43.0 + i * 0.1, 1200, 0.2, 0.98 + i * 0.02)
             for i in range(6)]

    def render(rs):
        data = [{"r": {"cat": {"name": n}, "neff": ne, "tot": t, "cr3": c, "n": 50},
                 "ratio": ra} for n, ne, t, c, ra in rs]
        prog = (js[cs:ce] + "\n" + js[qs:qe]
                + "\nfunction esc(s){return String(s).replace(/[&<>\"]/g,"
                  "c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}\n"
                + f"const ROWS={json.dumps(data, ensure_ascii=False)};\n"
                + "ROWS.forEach(d=>d.lv=concLevel(d.r.neff));\n"
                  "console.log(JSON.stringify({html:quadChartHTML(ROWS),"
                  "lv:ROWS.map(d=>d.lv),pad:QPAD,w:QW,h:QH}));")
        prog = prog.replace("\\\\", "\\")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(prog)
            path = f.name
        try:
            out = subprocess.run(["node", path], capture_output=True, text=True,
                                 timeout=30)
            if out.returncode != 0:
                check(False, f"분야 지도 JS 가 실행된다 ({out.stderr.strip()[:160]})")
                return None
            return json.loads(out.stdout)
        finally:
            try:
                __import__("os").unlink(path)
            except OSError:
                pass

    res = render(rows)
    if res is None:
        return

    check(len(set(res["lv"])) >= 2,
          "집중도 등급이 실측 분포에서 갈린다 "
          f"(받은 등급 {sorted(set(res['lv']))})")

    pad, x0, y0 = res["pad"], res["pad"]["l"], res["pad"]["t"]
    x1, y1 = res["w"] - pad["r"], res["h"] - pad["b"]
    dots = [tuple(map(float, m)) for m in _re.findall(
        r'<circle class="dot" cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', res["html"])]
    check(len(dots) == len(rows), f"분야 수만큼 원이 그려진다 ({len(dots)}/{len(rows)})")
    ys = [c[1] for c in dots]
    span = (max(ys) - min(ys)) / (y1 - y0) if dots else 0
    check(span >= 0.55,
          f"점이 y 축에 퍼진다 (세로로 판의 {span:.0%} 를 쓴다, 기준 55% 이상)")
    check(all(x0 <= cx - r and cx + r <= x1 and y0 <= cy - r and cy + r <= y1
              for cx, cy, r in dots),
          "원이 그림판 밖으로 삐져나가지 않는다 (사분면 설명 글자를 덮었었다)")

    # 이름표 겹침. 글자 폭은 그리는 쪽과 같은 어림(한글 10.5 / 그 밖 6)으로 잰다.
    def overlaps(html):
        labs = _re.findall(
            r'<text class="ql" x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)">([^<]+)</text>',
            html)
        boxes = []
        for sx, sy, anc, txt in labs:
            w = sum(10.5 if ord(ch) > 0x1100 else 6 for ch in txt)
            lx = float(sx) if anc == "start" else float(sx) - (w if anc == "end" else w / 2)
            boxes.append((lx, float(sy) - 10, w, 13))
        return labs, [(labs[i][3], labs[j][3])
                      for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                      if boxes[i][0] < boxes[j][0] + boxes[j][2]
                      and boxes[j][0] < boxes[i][0] + boxes[i][2]
                      and boxes[i][1] < boxes[j][1] + boxes[j][3]
                      and boxes[j][1] < boxes[i][1] + boxes[i][3]]

    labs, over = overlaps(res["html"])
    check(len(labs) == len(rows) and not over,
          f"이름표가 서로 겹치지 않는다 ({'겹침: ' + str(over[:2]) if over else '겹침 없음'})")

    res2 = render(crowd)
    if res2 is None:
        return
    labs2, over2 = overlaps(res2["html"])
    check(len(labs2) == len(crowd) and not over2,
          "실질 경쟁자 수가 거의 같은 분야가 몰려도 이름표가 겹치지 않는다 "
          f"({'겹침: ' + str(over2[:2]) if over2 else '겹침 없음'})")


def _supplier_checks(sr) -> None:
    """공급자 표(_supKind/suppliers)를 실제로 실행해 본다.

    이 부분은 파이썬 문자열 안의 JS 라 py_compile 이 봐 주지 않는다. 실제로
    한 번에 네 가지가 났다 — 선언 없는 SUP_COMPANY(페이지 전체가 죽었다),
    민간 시험연구소가 출연연으로 분류, 전역 .more 와 클래스 이름 충돌로 칩이
    줄 가운데로 밀림, 칩 숫자와 눌렀을 때 열리는 목록 건수 불일치. 화면으로만
    확인하면 다음에 또 난다.
    """
    import json
    import re as _re
    import shutil
    import subprocess
    import tempfile

    js = sr._JS
    # 1) 소스 수준 — node 가 없어도 도는 최소한의 방어선.
    check("const SUP_COMPANY" in js,
          "SUP_COMPANY 가 선언돼 있다 (없으면 페이지 전체가 죽는다)")
    check("class=\"cta more\"" not in js,
          "공급자 칩이 전역 '더 보기'(.more) 클래스를 쓰지 않는다")
    check("$('#guide').addEventListener('keydown'" in js
          or "$('#guide').onkeydown" in js,
          "공급자 칩에 키보드 활성화가 달려 있다")
    # 클릭 위임은 #results 가 아니라 #guide 여야 한다 (표가 #guide 안에 있다).
    gi = js.find("$('#guide').onclick")
    ri = js.find("$('#results').addEventListener('click'")
    sp = js.find("closest('[data-sup]')")
    check(gi >= 0 and sp > gi and (ri < 0 or not (ri < sp < js.find("$('#reset')"))),
          "[data-sup] 클릭 위임이 #guide 에 달려 있다")

    if not shutil.which("node"):
        check(True, "(node 없음 — JS 실행 검사는 건너뛴다)")
        return

    # 2) 실행 수준 — 함수를 그대로 떼어내 node 에서 돌린다.
    start = js.find("const SUP_KINDS")
    end = js.find("const SUP_TOP")
    check(start >= 0 and end > start, "공급자 블록을 소스에서 떼어낼 수 있다")
    if start < 0 or end <= start:
        return
    block = js[start:end]

    items = [
        # 대표 출원인 + 공동출원. 한국원자력연구원은 세 번째 자리에 있다.
        {"aName": "서울대학교산학협력단|한국과학기술원|한국원자력연구원",
         "aCountry": "KR", "category": "nuclear", "number": "P1"},
        {"aName": "한국원자력연구원", "aCountry": "KR",
         "category": "nuclear", "number": "P2"},
        {"aName": "주식회사 스탠더드시험연구소", "aCountry": "KR",
         "category": "nuclear", "number": "P3"},   # 민간 — 빠져야 한다
        {"aName": "주식회사동일기술공사", "aCountry": "KR",
         "category": "nuclear", "number": "P4"},   # 민간 — 빠져야 한다
        {"aName": "한국전력공사", "aCountry": "KR",
         "category": "nuclear", "number": "P5"},
        {"aName": "Siemens AG", "aCountry": "DE",
         "category": "nuclear", "number": "P6"},   # 해외 — 빠져야 한다
    ]
    prog = (block + "\nconst FEED={patents:{categories:[{key:'nuclear',"
            "name:'원전·SMR',emoji:'x'}]}};\n"
            f"const ITEMS={json.dumps(items, ensure_ascii=False)};\n"
            "const rows=suppliers(ITEMS);\n"
            "console.log(JSON.stringify(rows.map(r=>({cat:r.cat.key,n:r.n,"
            "total:r.total,orgs:r.orgs.map(o=>[o.name,o.cnt,o.kind.label])}))));")
    # site_render 의 JS 는 파이썬 문자열이라 정규식 이스케이프가 '\\s' 로 들어 있다.
    prog = prog.replace("\\\\", "\\")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(prog)
        path = f.name
    try:
        out = subprocess.run(["node", path], capture_output=True, text=True,
                             timeout=30)
        if out.returncode != 0:
            check(False, f"공급자 JS 가 실행된다 ({out.stderr.strip()[:160]})")
            return
        rows = json.loads(out.stdout)
    finally:
        try:
            __import__("os").unlink(path)
        except OSError:
            pass

    check(len(rows) == 1 and rows[0]["cat"] == "nuclear",
          "분야 한 줄이 나온다")
    orgs = {o[0]: (o[1], o[2]) for o in rows[0]["orgs"]}
    check("한국원자력연구원" in orgs and orgs["한국원자력연구원"][0] == 2,
          "공동출원에서 두 번째·세 번째 출원인도 세어진다 "
          f"(한국원자력연구원 {orgs.get('한국원자력연구원', ('?',))[0]}건)")
    check("주식회사 스탠더드시험연구소" not in orgs,
          "'주식회사 …시험연구소'는 출연연으로 분류되지 않는다")
    check("주식회사동일기술공사" not in orgs,
          "'주식회사…공사'는 공공기관으로 분류되지 않는다")
    check("Siemens AG" not in orgs, "해외 출원인은 빠진다")
    check(orgs.get("한국전력공사", (0, ""))[1] == "공공기관",
          "한국전력공사는 공공기관이다")
    check(orgs.get("서울대학교산학협력단", (0, ""))[1] == "대학",
          "산학협력단은 대학이다")
    # 합계는 '건수'다. 기관별 건수를 더하면 P1 이 세 번 세져 6이 된다.
    check(rows[0]["total"] == 3,
          f"분야 합계가 건수다 (공동출원 중복 없이 3건, 받은 값 {rows[0]['total']})")


def _fg_page(rows: list[tuple[str, str, str]], total: int) -> str:
    """해외 응답 흉내. 성공이면 resultCode 가 **비어 있다**(실측)."""
    body = "".join(
        f"<searchResult><ltrtno>{lit}</ltrtno><ipc>{ipc}</ipc>"
        f"<applicationNo>{lit[:8]}</applicationNo><registerNo></registerNo>"
        f"<publishrNo>{lit}</publishrNo><countryCode>{cc}</countryCode>"
        f"<applicationDate>20250301</applicationDate>"
        f"<openDate>20260715</openDate><registerDate></registerDate>"
        f"<applicant>Panasonic Holdings|Nichicon</applicant>"
        f"<inventors>A|B</inventors><openNumber>{lit}</openNumber>"
        f"<inventionName>POWER {lit}</inventionName></searchResult>"
        for lit, ipc, cc in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?><response><header>'
            "<resultCode></resultCode><resultMsg></resultMsg></header>"
            f"<body><items>{body}<colString>US</colString>"
            f"<totalSearchCount>{total}</totalSearchCount>"
            "</items></body></response>")


def _foreign_checks() -> None:
    """해외 수집기. 국내와 규칙이 뒤집힌 자리가 많아 회귀 검사가 특히 중요하다."""
    import xml.etree.ElementTree as ET
    from datetime import datetime

    import patent_source_foreign as fg
    import patent_source_kipris as kr

    print("\n[해외 백엔드]")
    orig_get, orig_key = fg._get, cfg.KIPRIS_KEY
    orig_cap, orig_rows = cfg.FOREIGN_PER_CAT, cfg.FOREIGN_ROWS
    cfg.KIPRIS_KEY = "TEST"
    calls: list[dict] = []
    try:
        # 이름·키 질의·파라미터가 국내와 섞이지 않았는지 (섞이면 조용히 빈 결과다)
        url = fg._url({"ipc": "H02M", "currentPage": "1"})
        check("ForeignPatentAdvencedSearchService" in url,
              "서비스 이름의 오타(Advenced)를 그대로 쓴다")
        check("ForeignPatentAdvancedSearchService" not in url,
              "철자를 고친 이름(Advanced)을 쓰지 않는다 — 그 경로는 없다")
        check("accessKey=" in url and "ServiceKey=" not in url,
              "키 질의 이름이 accessKey 다 (국내는 ServiceKey)")
        check("ipc=" in url and "ipcNumber=" not in url,
              "분류 파라미터가 ipc 다 (국내는 ipcNumber)")
        check("/openapi/rest/" in url, "기준 경로가 openapi/rest 다")

        # 성공 판정이 국내와 뒤집혀 있다 — 여기가 틀리면 정상 응답이 전부 오류가 된다
        ok = ET.fromstring(_fg_page([("202600213551A1", "H02M1/10", "US")], 1))
        check(fg._total(ok) == 1, "totalSearchCount 를 읽는다 (국내는 totalCount)")
        bad = ('<?xml version="1.0"?><response><header><resultCode>11'
               "</resultCode><resultMsg>No Mandatory Request Parameters Error"
               "</resultMsg></header></response>")

        import urllib.request
        saved_open = urllib.request.urlopen

        class _R:
            def __init__(self, s): self.s = s
            def read(self): return self.s.encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        urllib.request.urlopen = lambda *a, **k: _R(bad)
        try:
            fg._get({"ipc": "H02M"})
            check(False, "resultCode 가 채워져 오면 오류로 본다")
        except RuntimeError as e:
            check("11" in str(e), f"resultCode 가 채워져 오면 오류로 본다 ({e})")
        finally:
            urllib.request.urlopen = saved_open

        urllib.request.urlopen = lambda *a, **k: _R(
            _fg_page([("202600213551A1", "H02M1/10", "US")], 1))
        try:
            fg._get({"ipc": "H02M"})
            check(True, "resultCode 가 비어 오면 정상으로 본다 (국내와 정반대)")
        except Exception as e:
            check(False, f"resultCode 가 비어 오면 정상으로 본다 ({e})")
        finally:
            urllib.request.urlopen = saved_open

        # ── 쪽넘김: currentPage 는 '시작 위치'다 ──────────────────────
        # 쪽 번호로 넘기면 각 쪽이 겹치면서 뒤쪽 자료에 영원히 닿지 못한다.
        cfg.FOREIGN_ROWS, cfg.FOREIGN_PER_CAT = 50, 500
        universe = [(f"20260021{i:04d}A1", "H02M1/10", "US") for i in range(120)]

        def fake(params, timeout=None):
            calls.append(dict(params))
            start = int(params["currentPage"])
            n = int(params["docsCount"])
            return ET.fromstring(
                _fg_page(universe[start - 1:start - 1 + n], len(universe)))
        fg._get = fake

        cat = {"key": "mega", "name": "전력반도체", "emoji": "x",
               "ipc": ["H02M"], "cpc": ["H02M"]}
        items, total, capped = fg._sweep(cat, "20260601~20260825")
        starts = [int(c["currentPage"]) for c in calls]
        check(starts[:3] == [1, 51, 101],
              f"시작 위치를 쪽 크기만큼 더해 넘긴다 (보낸 값 {starts[:3]})")
        check(len(items) == len(universe),
              f"120건을 하나도 빠뜨리지 않고 받는다 (받은 값 {len(items)}건)")
        check(len({i['number'] for i in items}) == len(items),
              "받은 목록에 중복이 없다")
        check(total == len(universe), f"전체 건수를 읽는다 ({total})")
        check(not capped, "상한에 걸리지 않았다")

        # 항목 스키마가 국내와 같아야 목록에 그대로 섞인다
        need = {"number", "title", "assignee", "pub_date", "office", "cpc",
                "category", "applicant", "country", "flag", "url"}
        check(not (need - set(items[0])),
              f"항목 스키마가 국내와 같다 (누락 {need - set(items[0]) or '없음'})")
        check(items[0]["number"].startswith("US"),
              f"공개번호 앞에 나라를 붙인다 ({items[0]['number']})")
        check(items[0]["office"] == "US", "office 가 공개국이다")

        # 모르는 해외 출원인을 한국으로 떨어뜨리면 '국내 공급자' 표가 오염된다
        n, r, f = fg._identify_foreign("Nichicon Corporation")
        check(r == "" and f == "",
              f"큐레이션에 없는 해외 출원인의 국적은 비운다 (받은 값 {r!r})")
        n2, r2, _ = fg._identify_foreign("Siemens Energy AG")
        check(r2 and r2 != "KR",
              f"큐레이션에 있으면 그 국적을 쓴다 ({n2} → {r2})")

        # 일본 공보는 회사 이름이 일본어로 온다. 영문 별칭만 두면 같은 회사가
        # 둘로 갈린다 — 실측에서 Toyota 가 영문 245 + 일본어 222 로 쪼개져
        # 랭킹이 절반만 반영됐다(국내에서 겪은 '엘지 ≠ LG' 와 같은 문제).
        for jp, want in (("トヨタ自動車株式会社", "Toyota"),
                         ("株式会社東芝", "Toshiba"),
                         ("パナソニックIPマネジメント株式会社", "Panasonic"),
                         ("三菱電機株式会社", "Mitsubishi Electric"),
                         ("株式会社日立製作所", "Hitachi")):
            got = kr._identify(jp)[0]
            check(got == want, f"일본어 표기가 붙는다: {jp} → {got}")

        # KIPRIS 는 실체참조를 두 번 감싼다. 한 번만 풀면 화면에 글자로 남는다.
        check(kr._unescape("XI&apos;AN JIAOTONG") == "XI'AN JIAOTONG",
              "남은 &apos; 를 푼다")
        check(kr._unescape("Fitch &amp;#x26; Flannery") == "Fitch & Flannery",
              "두 번 감싼 &#x26; 도 푼다")
        check(kr._unescape("정상 이름 & 회사") == "정상 이름 & 회사",
              "이미 정상인 글자는 건드리지 않는다")

        # 수집기에서 푸는 것만으로는 부족하다 — 아카이브는 누적이라 이미 저장된
        # 항목은 그대로다(실측: 고친 뒤에도 저장분 430건에 XI&apos;AN 이 남았다).
        # 그래서 그리기 직전에 한 번 더 푼다.
        import site_render as _sr
        check(_sr._plain("XI&apos;AN JIAOTONG") == "XI'AN JIAOTONG",
              "화면에 그리기 직전에도 실체참조를 푼다 (옛 저장분까지 고쳐진다)")
        check(_sr._plain("Vorwerk &amp; Co.") == "Vorwerk & Co.",
              "&amp; 도 화면에서 풀린다")
        check('_plain(p.get("applicant")' in _sr.__doc__ or
              '_plain' in open("site_render.py", encoding="utf-8").read()
              .split("def _patent_feed")[1][:3000],
              "_patent_feed 가 이름을 그리기 전에 _plain 을 거친다")

        # 국내 수집이 살아 있는데 해외가 죽으면, 국내까지 잃으면 안 된다
        import patent_source_kipris as ks
        saved_fg = fg.collect
        fg.collect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("해외 다운"))
        try:
            kept = [{"number": "KR1", "office": "KR", "applicant": "한국전력공사"}]
            out, st = ks._add_foreign(datetime(2026, 8, 25), kept,
                                      {"totals": {"한국전력공사": 1}})
            check(out == kept and "foreignError" in st,
                  "해외가 실패해도 국내 결과를 지키고 그 사실을 남긴다")
        finally:
            fg.collect = saved_fg

        # 공개국 축이 화면이 읽는 모양으로 나오는지 — 모양이 다르면 오류 없이
        # 표만 빈다(officeCounts[출원인][특허청]).
        saved_fg2 = fg.collect
        fg.collect = lambda *a, **k: ([{
            "number": "US1", "office": "US", "applicant": "Siemens"}], {})
        try:
            out, st = ks._add_foreign(
                datetime(2026, 8, 25),
                [{"number": "KR1", "office": "KR", "applicant": "한국전력공사"}],
                {"totals": {}})
            off = st.get("offices") or {}
            check(off.get("한국전력공사", {}).get("KR") == 1
                  and off.get("Siemens", {}).get("US") == 1,
                  f"공개국 집계가 [출원인][특허청] 모양이다 ({off})")
            check(st.get("replaceOffices") is True,
                  "공개국 집계도 전수라 갈아 끼우게 표시한다")
            import patent_archive as pa
            store = {"totals": {}, "offices": {"Siemens": {"WO": 183}},
                     "updated": {}}
            pa.merge_stats(store, {"totals": {}, "offices": off,
                                   "replaceTotals": True,
                                   "replaceOffices": True})
            check("WO" not in store["offices"].get("Siemens", {}),
                  "옛 공개국 수치(OPS 시절)가 실제로 버려진다")
        finally:
            fg.collect = saved_fg
    finally:
        fg._get, cfg.KIPRIS_KEY = orig_get, orig_key
        cfg.FOREIGN_PER_CAT, cfg.FOREIGN_ROWS = orig_cap, orig_rows


def _kipris_checks() -> None:
    """KIPRIS 백엔드의 라이브 경로를 네트워크 없이 실제로 실행한다.

    py_compile 로는 못 잡는 것들을 잡으려는 것이다 — OPS 때 _live_collect 의
    NameError 가 월요일 실행에서야 드러난 적이 있다. 여기서 확인하는 것은
    '수집이 도는가'가 아니라 **site_render 가 기대하는 계약을 지키는가**다.
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime
    import patent_source_kipris as ks

    import urllib.request

    print("\n[KIPRIS 백엔드]")
    orig_get, orig_key = ks._get, cfg.KIPRIS_KEY
    orig_lim, orig_open = cfg.KIPRIS_CPC_LIMIT, urllib.request.urlopen
    # 이 검사는 **국내 경로**를 본다. collect() 는 이제 해외까지 붙이므로 꺼 둔다 —
    # 켜 두면 해외 요청이 이 검사의 스텁으로 새어 들어와 CPC 호출 수가 어긋난다
    # (실제로 5건이어야 할 것이 22건으로 찍혔다). 해외는 _foreign_checks 가 본다.
    orig_fg = cfg.FOREIGN
    cfg.FOREIGN = False
    cfg.KIPRIS_KEY = "TEST"
    calls: list[dict] = []
    cpc_calls: list[str] = []
    try:
        # CPC 보강은 urlopen 을 직접 쓴다 → 여기서도 막지 않으면 스모크 테스트가
        # 네트워크를 탄다(러너에서 수집 전에 도는 검사라 절대 나가면 안 된다).
        cfg.KIPRIS_CPC_LIMIT = 5
        _CPC = ('<?xml version="1.0"?><response><body><items><patentCpcInfo>'
                "<CooperativepatentclassificationNumber>Y04S 10/50"
                "</CooperativepatentclassificationNumber></patentCpcInfo>"
                "</items></body></response>")

        class _CpcResp:
            def read(self): return _CPC.encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_open(req, *a, **k):
            cpc_calls.append(getattr(req, "full_url", str(req)))
            return _CpcResp()
        urllib.request.urlopen = _fake_open

        def fake(op, params, timeout=None):
            calls.append(dict(params, _op=op))
            who = ["한국전력공사", "인천대학교 산학협력단", "주식회사 현대케피코"]
            n = len(calls)
            return ET.fromstring(_kipris_page(
                [(n * 10 + k, who[k], params["ipcNumber"]) for k in range(3)], 42))
        ks._get = fake

        items, mock, stats = ks.collect(datetime(2026, 8, 25))
        check(not mock, "라이브 경로로 돈다 (MOCK 폴백이 아니다)")
        check(bool(items), f"수집 결과가 있다 ({len(items)}건)")

        need = {"number", "title", "assignee", "pub_date", "office", "cpc",
                "category", "applicant", "country", "flag", "url"}
        missing = need - set(items[0])
        check(not missing, f"항목 스키마가 site_render 계약을 지킨다 (누락 {missing or '없음'})")

        keys = {c["key"] for c in cfg.CATEGORIES}
        check(all(i["category"] in keys for i in items),
              "모든 항목이 8대 분야 중 하나로 분류된다")
        check(all(i["office"] == "KR" for i in items), "국내 공보이므로 office 는 전부 KR")
        nums = [i["number"] for i in items]
        check(len(nums) == len(set(nums)), "공개번호가 중복되지 않는다")
        check(all(i["url"].startswith("https://") for i in items),
              "카드 링크가 전부 https 다")
        check(len({i["url"] for i in items}) == len(items),
              "url 이 항목마다 다르다 (읽음 상태의 키라 겹치면 한꺼번에 토글된다)")

        # 총계는 따로 조회하지 않고 모은 것을 센다 — 그 약속이 지켜지는지 본다.
        check(sum(stats["totals"].values()) == len(items),
              "출원인 총계의 합 = 수집 건수 (표본과 전수가 어긋나지 않는다)")
        # 전수를 가져오므로 집계는 병합이 아니라 대체여야 한다. 병합하면 OPS 시절
        # 값(전 세계·CPC 기준)이 남아 단위가 다른 수치와 한 표에 섞인다(첫 실전
        # 실행에서 실제로 그랬다 — Siemens 183 같은 옛 수치가 그대로 남았다).
        check(stats.get("replaceTotals") is True,
              "집계를 대체로 표시한다 (옛 OPS 수치가 섞이지 않는다)")
        import patent_archive as pa
        store = {"totals": {"Siemens": 183}, "updated": {"Siemens": "2026-08-03"},
                 "offices": {}}
        pa.merge_stats(store, stats, "2026-08-25")
        check("Siemens" not in store["totals"] or store["totals"]["Siemens"] != 183,
              "merge_stats 가 옛 값을 실제로 버린다")

        # 공동출원('|')과 한글 법인명이 같은 회사를 둘로 가르면 랭킹이 거짓이 된다.
        check(ks._split_applicants("현대자동차주식회사|기아 주식회사")
              == ["현대자동차주식회사", "기아 주식회사"],
              "공동출원인을 '|' 로 나눈다")
        for raw, want in (("주식회사 엘지에너지솔루션", "LG에너지솔루션"),
                          ("삼성에스디아이 주식회사", "삼성SDI"),
                          ("도요타 지도샤(주)", "Toyota"),
                          ("컨템포러리 엠퍼렉스 테크놀로지 씨오., 리미티드", "CATL")):
            check(ks._identify(raw)[0] == want,
                  f"한글 법인명이 붙는다: {raw} → {want}")
        check(all(p.get("openDate") for p in calls),
              "모든 질의에 공개일 범위가 들어간다 (기간 없이 전수를 긁지 않는다)")
        check(all("~" in p["openDate"] for p in calls),
              "공개일 범위 표기가 'YYYYMMDD~YYYYMMDD' 다 (실측 형식)")
        check(not any("cpcNumber" in p for p in calls),
              "cpcNumber 를 보내지 않는다 (이 API 에 없는 파라미터 — 실측)")

        # CPC 보강: 검색으로는 못 잡는 Y04S 를 출원번호로 되받아 분류에 반영한다.
        check(len(cpc_calls) == cfg.KIPRIS_CPC_LIMIT,
              f"CPC 보강이 상한만큼만 돈다 ({len(cpc_calls)}건)")
        check(all("patentCpcInfo" in u and "applicationNumber=" in u
                  for u in cpc_calls),
              "CPC 는 출원번호로 조회한다 (공개번호가 아니다)")
        check(all(cfg.KIPRIS_CPC_KEYPARAM + "=" in u for u in cpc_calls),
              f"CPC 조회의 키 질의 이름이 {cfg.KIPRIS_CPC_KEYPARAM} 다 (계열이 다르다)")
        enriched = [i for i in items if any(c.startswith("Y04S") for c in i["cpc"])]
        check(bool(enriched), f"보강된 건에 CPC 가 들어간다 ({len(enriched)}건)")
        check(all(i["category"] == "meter" for i in enriched),
              "Y04S 가 붙으면 계량·스마트그리드로 분류된다 (IPC 로는 못 잡는 코드)")

        # 국내 전용이라 특허청 축은 만들지 않는다. 빈 dict 이어야 기존 stats 가
        # 덮이지 않는다(merge_stats 계약).
        check(ks.collect_offices(datetime(2026, 8, 25)) == {},
              "collect_offices 는 빈 dict (기존 공개국 집계를 덮지 않는다)")

        # 경로가 틀리면 KIPRIS 는 HTTP 200 + 포털 HTML 을 준다. 그때 XML 파싱이
        # 깨지는데, 사람이 읽을 수 있는 오류로 바뀌는지 확인한다(실측으로 물렸던 함정).
        ks._get = orig_get
        class _Fake:
            def read(self): return b"<!doctype html><html>\xed\x8e\x98\xec\x9d\xb4\xec\xa7\x80"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        saved_open = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _Fake()
        try:
            ks._get("getAdvancedSearch", {"ipcNumber": "H02M"})
            check(False, "포털 HTML 응답이 오류로 드러난다")
        except RuntimeError as e:
            check("XML" in str(e), f"포털 HTML 응답이 오류로 드러난다 ({e})")
        finally:
            urllib.request.urlopen = saved_open

        # 접두당 상한 판정. 예전에는 '접두별 totalCount 의 합 > 상한' 으로 짐작해
        # 두 방향으로 다 틀릴 수 있었다. 두 경우를 실제로 돌려 확인한다.
        saved_cap = cfg.KIPRIS_PER_CAT
        try:
            cfg.KIPRIS_PER_CAT = 6
            three = {"ipc": ["A", "B", "C"], "cpc": ["A"], "key": "renew",
                     "name": "테스트", "emoji": "x"}

            # ① 접두마다 2건씩(합 6, 상한과 같음) — 아무것도 잘리지 않았다.
            seq = [0]
            def small(op, params, timeout=None):
                seq[0] += 1
                base = seq[0] * 100
                return ET.fromstring(_kipris_page(
                    [(base + k, "한국전력공사", params["ipcNumber"])
                     for k in range(2)], 900))     # totalCount 는 크게 900
            ks._get = small
            _, _, hit = ks._sweep_category(three, "20260101~20260301")
            check(not hit,
                  "접두별 합계가 커도 실제로 안 잘렸으면 상한 경고가 없다"
                  " (옛 방식이면 거짓 경고)")

            # ② 한 접두가 상한까지 차오른다 — 잘렸다.
            seq2 = [0]
            def big(op, params, timeout=None):
                seq2[0] += 1
                base = seq2[0] * 1000
                return ET.fromstring(_kipris_page(
                    [(base + k, "한국전력공사", params["ipcNumber"])
                     for k in range(cfg.KIPRIS_ROWS)], 900))
            ks._get = big
            _, _, hit2 = ks._sweep_category(three, "20260101~20260301")
            check(hit2, "접두 하나가 상한에 닿으면 잘린 것으로 보고한다")
        finally:
            cfg.KIPRIS_PER_CAT = saved_cap
            ks._get = orig_get
    finally:
        ks._get, cfg.KIPRIS_KEY = orig_get, orig_key
        cfg.KIPRIS_CPC_LIMIT = orig_lim
        cfg.FOREIGN = orig_fg
        urllib.request.urlopen = orig_open


def main() -> int:
    today = datetime(2026, 7, 27)
    orig = (ps._search, ps._get_token, cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY)
    ps._get_token = lambda: "stub-token"
    cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = "k", "s", 0.0
    try:
        print("· _live_collect (주간 특허 수집 경로)")
        stub = Stub()
        ps._search = stub
        items, stats = ps._live_collect(today)
        check(bool(items), f"특허를 수집한다 ({len(items)}건)")
        check(isinstance(stats.get("totals"), dict) and bool(stats["totals"]),
              f"출원인 총계를 담는다 ({len(stats.get('totals', {}))}곳)")
        check(all(i.get("category") and i.get("number") for i in items),
              "모든 항목에 분야·공개번호가 있다")

        print("· 국내(KR) 공개 추가 수집 (해외 출원인 한정)")
        # KR 전용 질의(pn any "KR")가 해외 출원인에게만 나가는지, 그 결과가 목록에
        # 더해지는지 확인한다. 스텁은 질의 문자열을 기록만 하고 문서를 돌려준다.
        seen_cql: list[str] = []

        class KrStub(Stub):
            def __call__(self, token, cql, start, end, timeout=None):
                seen_cql.append(cql)
                return super().__call__(token, cql, start, end, timeout)

        ps._search = KrStub(total=3, per_call=3)
        seen, collected = set(), []
        n = ps._collect_kr("stub-token", today, seen, collected)
        check(bool(seen_cql) and all('pn any "KR"' in q for q in seen_cql),
              "모든 질의가 KR 공개로 한정된다")
        kr_names = {a["name"] for a in cfg.APPLICANTS if a["region"] == "KR"}
        foreign = {a["name"] for a in cfg.APPLICANTS if a["region"] != "KR"}
        kr_terms = [a["q"] for a in cfg.APPLICANTS if a["region"] == "KR"]
        kr_terms = [t for q in kr_terms for t in ([q] if isinstance(q, str) else q)]
        check(not any(f'pa="{t}"' in q for q in seen_cql for t in kr_terms),
              f"국내 출원인({len(kr_names)}곳)에게는 질의하지 않는다")
        check(n > 0 and len(collected) == n, f"국내 공개를 목록에 더한다 ({n}건)")
        check(all(i.get("applicant") in foreign for i in collected),
              "더해진 항목은 전부 해외 출원인 것이다")
        per_ap: dict[str, int] = {}
        for i in collected:
            per_ap[i["applicant"]] = per_ap.get(i["applicant"], 0) + 1
        check(max(per_ap.values()) <= cfg.KR_LIMIT,
              f"출원인당 상한({cfg.KR_LIMIT})을 넘지 않는다")

        print("· 주간 수집 시작점 회전 (뒤쪽 출원인이 영구히 굶지 않는다)")
        n = len(cfg.APPLICANTS)
        starts, weeks = set(), [datetime(2026, 1, 5) + timedelta(weeks=w)
                                for w in range(12)]
        ok_seq = True
        for d in weeks:
            o = ps._collect_order(d)
            check_len = len(o) == n and {a["name"] for a in o} == \
                {a["name"] for a in cfg.APPLICANTS}
            if not check_len:
                ok_seq = False
                break
            starts.add(o[0]["name"])
            # seq 로 묶인 항목은 앞 항목보다 뒤에 있어야 한다
            pos = {a["name"]: i for i, a in enumerate(o)}
            for i, a in enumerate(cfg.APPLICANTS):
                if a.get("seq") and i > 0:
                    if pos[a["name"]] < pos[cfg.APPLICANTS[i - 1]["name"]]:
                        ok_seq = False
        check(check_len, "회전해도 출원인이 빠지거나 늘지 않는다")
        check(len(starts) >= 3, f"주마다 시작점이 바뀐다 (12주에 {len(starts)}가지)")
        check(ok_seq, "dedup 우선순위 묶음(seq)이 회전에 끊기지 않는다")
        # 한 실행이 소화하는 만큼(COLLECT_ROTATE) 씩 밀리면 몇 주 안에 전원이 선두권에 든다
        covered, span = set(), max(1, cfg.COLLECT_ROTATE)
        for d in weeks[:4]:
            covered |= {a["name"] for a in ps._collect_order(d)[:span]}
        check(len(covered) >= min(n, span * 3),
              f"4주 안에 {len(covered)}/{n}곳이 앞 {span}순위 안에 든다")

        print("· OPS 오류 사유 노출 (401 이 왜 났는지 로그에 남는다)")
        import io as _io
        import urllib.error as _ue

        class _Err(_ue.HTTPError):
            def __init__(self, code, body):
                super().__init__("u", code, "x", {}, _io.BytesIO(body.encode()))

        # 이 파일은 앞에서 _get_token·_search 를 스텁으로 바꿔 놓았다 → 진짜 함수를
        # 잠시 되돌려 놓고 검사한다(안 그러면 스텁을 시험하는 꼴이 된다).
        _orig_open = ps.urllib.request.urlopen
        _stub_search, _stub_token = ps._search, ps._get_token
        ps._search, ps._get_token = orig[0], orig[1]
        try:
            ps.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
                _Err(401, "<error><code>CLIENT.InvalidCredentials</code></error>"))
            try:
                ps._get_token()
                msg = ""
            except Exception as e:
                msg = str(e)
            check("CLIENT.InvalidCredentials" in msg,
                  "토큰 실패 시 EPO 가 준 사유가 메시지에 담긴다")
            ps.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
                _Err(403, "<fault><code>SERVER.QuotaPerHour</code></fault>"))
            try:
                ps._search("t", "q", 1, 25)
                msg2 = ""
            except Exception as e:
                msg2 = str(e)
                # 사유를 붙이면서 '403' 을 잃으면 쿼터 판정이 깨져 계속 두드리게 된다.
                check(ps._is_quota(e), "사유를 붙여도 쿼터(403) 판정이 유지된다")
            check("QuotaPerHour" in msg2, "검색 실패 사유도 메시지에 담긴다")
        finally:
            ps.urllib.request.urlopen = _orig_open
            ps._search, ps._get_token = _stub_search, _stub_token

        print("· 출원인 질의어 겹침 (총계 부풀림 방지)")
        # 목록은 공개번호 dedup 이 막아 주지만, 총계(stats)는 출원인별 독립 질의라
        # 막을 방법이 없다. 한 출원인의 검색 어구가 다른 출원인의 이름·어구에 통째로
        # 들어 있으면 그 회사 문서까지 세어 총계가 부풀려진다 — 실측으로 q="Siemens"
        # 가 Siemens Energy(53)·Gamesa(75) 를 삼켜 183 이 나왔다. 분야별 경쟁 구도의
        # 지분이 그만큼 틀어지므로 설정 단계에서 막는다.
        def _qs(a):
            q = a["q"]
            return [q] if isinstance(q, str) else list(q)

        overlaps = []
        for a in cfg.APPLICANTS:
            for b in cfg.APPLICANTS:
                if a is b:
                    continue
                for x in _qs(a):
                    for y in _qs(b) + [b["name"]]:
                        if x.lower() != y.lower() and x.lower() in y.lower():
                            overlaps.append(f'{a["name"]} q="{x}" ⊂ {b["name"]}("{y}")')
        for o in overlaps:
            print("    " + o)
        check(not overlaps,
              f"어떤 질의어도 다른 출원인을 삼키지 않는다 ({len(cfg.APPLICANTS)}곳)")

        print("· collect_offices (매일 공개국 집계 경로)")
        ps._search = Stub(total=5, per_call=1)
        st = ps.collect_offices(today)
        check(bool(st.get("totals")), f"총계 갱신 ({len(st.get('totals', {}))}곳)")
        check(bool(st.get("offices")), f"공개국 집계 갱신 ({len(st.get('offices', {}))}곳)")
        check(len(st.get("totals", {})) <= cfg.OFFICE_BATCH,
              f"하루 배치 상한({cfg.OFFICE_BATCH})을 넘지 않는다")

        print("· 집계 병합 (부분 실행이 기존 값을 지우지 않는다)")
        import patent_archive as pa
        store = {"totals": {"기존": 1}, "offices": {"기존": {"KR": 1}}, "updated": {}}
        pa.merge_stats(store, st, "2026-07-27")
        check(store["totals"].get("기존") == 1, "이전 출원인 값이 남는다")
        check(len(store["totals"]) > 1, "새 값이 더해진다")

        print("· 뉴스 아카이브 중복 판정·MOCK 표시")
        import news_archive as na
        # URL 동일 판정: 제목이 완전히 달라도 같은 기사면 다시 담기지 않아야 한다.
        days = {"2026-07-28": {"date": "2026-07-28", "articles": [
            {"title": "제목 A", "url": "https://ex.com/same"}]}}
        _, n = na.merge_today(days, "2026-07-29", [
            {"title": "완전히 딴판인 제목 B 입니다", "url": "https://ex.com/same"}], False)
        check(n == 0, "같은 URL 기사는 제목이 달라도 다시 담지 않는다")
        _, n = na.merge_today(days, "2026-07-29", [
            {"title": "아주 새로운 기사 제목 하나", "url": "https://ex.com/new"}], False)
        check(n == 1, "새 기사는 정상적으로 담긴다")
        # 하루 안에 라이브와 MOCK 이 섞여도 실데이터에 샘플 표시가 붙으면 안 된다.
        d2 = {}
        na.merge_today(d2, "2026-07-29", [{"title": "실데이터 기사", "url": "u1"}], False)
        na.merge_today(d2, "2026-07-29", [{"title": "샘플 기사", "url": "u2"}], True)
        arts = d2["2026-07-29"]["articles"]
        check(not arts[0].get("mock") and arts[1].get("mock") is True,
              "MOCK 표시는 항목별로 남는다")

        print("· 링크 스킴 제한 (javascript: 차단)")
        import re as _re
        import site_render as sr
        hrefs = _re.findall(r"href=\"'\+esc\((\w+)\(", sr._JS)
        raw = _re.findall(r"href=\"'\+esc\((?!safeUrl)", sr._JS)
        check("const safeUrl" in sr._JS, "safeUrl 헬퍼가 있다")
        check(bool(hrefs) and not raw,
              f"모든 링크 href 가 safeUrl 을 거친다 ({len(hrefs)}곳)")

        print("· 분야별 국내 공급자 (site_render 안의 JS)")
        _supplier_checks(sr)

        print("· 분야 지도 (축·등급·이름표 배치)")
        _quad_checks(sr)

        print("· 지연 로딩 (목록 분할)")
        _lazy_checks(sr)

        print("· 탭 아이콘(파비콘)")
        _favicon_checks(sr)

        print("· 거래·지원 안내 데이터 (사람이 관리하는 상수)")
        import ip_guide as ig
        ln = ig.links()
        check(bool(ln) and all(l.get("label") and l.get("url") for l in ln),
              f"카드 링크에 이름·URL 이 다 있다 ({len(ln)}종)")
        check(all("{n}" in l["url"] for l in ln),
              "카드 링크 URL 에 공개번호 자리({n})가 있다")
        allitems = [i for g in ig.GUIDE for i in g["items"]]
        shown = [i for g in ig.guide() for i in g["items"]]
        check(bool(ig.guide()) and bool(shown),
              f"안내 항목이 있다 ({len(ig.guide())}묶음 {len(shown)}곳 표시"
              f" · 주소 미확인 {len(ig.pending())}곳)")
        check(all(i.get("name") and i.get("org") and i.get("what") for i in allitems),
              "뼈대 항목도 이름·기관·설명은 다 갖춘다")
        # 주소를 못 채운 항목이 화면으로 새면 안 된다 — 확인 안 된 링크를 기관
        # 사이트에 올리지 않으려고 url 유무로 거르는 구조다.
        check(all(i.get("url") for i in shown), "표시되는 항목은 전부 주소가 있다")
        check(len(shown) + len(ig.pending()) == len(allitems),
              "표시 + 미확인 = 전체 (조용히 사라지는 항목이 없다)")
        urls = [l["url"] for l in ln] + [i["url"] for i in shown]
        check(all(u.startswith("https://") for u in urls),
              f"모든 링크가 https 다 ({len(urls)}개)")
    finally:
        ps._search, ps._get_token = orig[0], orig[1]
        cfg.OPS_KEY, cfg.OPS_SECRET, cfg.REQUEST_DELAY = orig[2], orig[3], orig[4]

    _kipris_checks()
    _foreign_checks()

    print(f"\n{'실패 ' + str(len(FAILS)) + '건' if FAILS else '전부 통과'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
