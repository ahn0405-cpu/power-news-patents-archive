"""국유판매기술(특허기술거래) 수집 — **국내 PC에서 실행**해 결과만 저장소에 올린다.

왜 로컬인가: apis.data.go.kr 은 해외 IP 를 막는다(실측 — GitHub Actions 러너에서
DNS 는 풀리는데 TCP 443·80 이 모두 타임아웃, 국내 회선에서는 응답 옴). 그래서 이
수집만 자동화 밖에 둔다. 만들어진 JSON 을 커밋하면 사이트 빌드가 그대로 읽는다.

국유특허는 권리자가 국가라 협상 상대와 창구가 명확하다. 특히 **무상**은 중소기업이
비용 없이 실시할 수 있어, 민간 특허 거래보다 진입 장벽이 훨씬 낮다.

사용:
    set DATA_GO_KR_KEY=발급키          (PowerShell: $env:DATA_GO_KR_KEY="...")
    python fetch_staown.py
    git add site/data/staown.json && git commit -m "국유판매기술 갱신" && git push

옵션(환경변수):
    STAOWN_KEYWORDS   검색어(공백 구분). 비우면 아래 기본 전력 키워드
    STAOWN_MAX_CALLS  요청 상한(기본 60). 포털 일일 트래픽이 오퍼레이션당 100회다.
    STAOWN_OUT        저장 경로(기본 staown.json — 저장소 루트)

주의: 이 스크립트는 실제 응답을 아직 보지 못한 상태에서 작성했다. 그래서 응답
필드 이름에 기대지 않고 XML 을 그대로 dict 로 옮긴다. 한 번 돌려 보고 실제 필드가
확인되면 화면 쪽에서 필요한 것만 골라 쓴다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

ENDPOINT = "https://apis.data.go.kr/1431000/StaownTradePatentInfoService"
KST = timezone(timedelta(hours=9))

# 발명의 명칭으로 찾으므로(sel_title), 전력 분야를 넓게 덮는 낱말을 쓴다.
# 너무 좁으면 놓치고, 너무 넓으면(예: '장치') 무관한 것이 쏟아진다.
DEFAULT_KEYWORDS = [
    "전력", "전기", "발전", "송전", "배전", "변압", "개폐",
    "태양광", "풍력", "축전", "전지", "계량", "절연", "전동기",
]
TIMEOUT = 30
DELAY = 0.4          # 연속 호출 사이 간격(서버 배려)
PER_PAGE = 100       # 한 번에 받는 수 — 요청 수를 줄이는 게 트래픽 상한에 유리하다


def _get(op: str, key: str, params: dict) -> str:
    q = "".join(f"&{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{ENDPOINT}/{op}?serviceKey={key}{q}"
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    # 5xx 는 서버가 잠깐 흔들린 것이라 한 번만 쉬고 다시 본다(실측으로 503 이
    # 한 번 났고, 그 낱말만 통째로 빠졌다). 그 이상은 매달리지 않는다.
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if attempt == 1 and 500 <= e.code < 600:
                time.sleep(3)
                continue
            raise
    return ""


def _items(xml: str) -> list[dict]:
    """응답 XML → 항목 dict 목록. 필드 이름을 모르므로 있는 그대로 옮긴다.

    공공데이터포털 표준은 <item> 반복이다. 그 이름이 아니면, 같은 태그가 여러 번
    나오는 가장 안쪽 묶음을 항목으로 본다.
    """
    root = ET.fromstring(xml)
    nodes = root.findall(".//item")
    if not nodes:
        best, bestn = None, 1
        for parent in root.iter():
            counts: dict[str, int] = {}
            for c in parent:
                counts[c.tag] = counts.get(c.tag, 0) + 1
            for tag, n in counts.items():
                if n > bestn:
                    best, bestn = [c for c in parent if c.tag == tag], n
        nodes = best or []
    out = []
    for n in nodes:
        d = {c.tag: (c.text or "").strip() for c in n}
        if any(d.values()):
            out.append(d)
    return out


def _err(xml: str) -> str:
    """오류 응답이면 사람이 읽을 메시지, 아니면 빈 문자열."""
    for tag in ("errMsg", "returnAuthMsg", "resultMsg", "returnReasonCode"):
        i = xml.find(f"<{tag}>")
        if i >= 0:
            j = xml.find(f"</{tag}>", i)
            val = xml[i + len(tag) + 2:j].strip()
            if val and val not in ("NORMAL SERVICE.", "00"):
                return f"{tag}={val}"
    return ""


def _seq(item: dict) -> str:
    """항목의 고유키 추정. 'seq' 가 들어간 필드를 우선 쓰고, 없으면 전체를 키로.

    요청 파라미터는 스네이크(sel_seq)인데 응답 필드는 카멜(selSeq)이라 이름이
    엇갈린다 → 대소문자를 지우고 'seq' 포함 여부로 찾는다.
    """
    for k, v in item.items():
        if "seq" in k.lower() and v:
            return v
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _relevant(title: str, kw: str) -> bool:
    """제목 검색이 부분일치라 엉뚱한 게 딸려 온다 → 낱말 첫머리인지로 거른다.

    실측('전력' 6건): '회전력을 이용한 양식김의 부착력 테스트 장치', '회전자석의
    기전력에 의한…' 두 건이 '회전력'·'기전력' 때문에 걸렸다. 낱말 첫머리 규칙을
    적용하면 이 둘만 정확히 떨어지고 나머지 넷은 남는다.

    다만 '자가발전형' 처럼 정당한데 탈락하는 경우도 있어, 떨어뜨린 것도 버리지
    않고 따로 담는다(규칙을 고칠 때 근거가 된다).
    """
    for tok in title.replace(",", " ").replace("(", " ").replace(")", " ").split():
        if tok.startswith(kw):
            return True
    return False


def _title(item: dict) -> str:
    for k, v in item.items():
        if "title" in k.lower():
            return v or ""
    return ""


def collect(key: str, keywords: list[str], max_calls: int,
            detail: bool = True) -> dict:
    """무상·유상 목록을 훑고, 걸러 남은 것만 상세를 채운다.

    목록에는 제목·일련번호·등록일뿐이라 '누구에게 받는지' 를 알 수 없다.
    상세(getFree/PayTLDetail)를 불러야 권리자(pemUser)·출원번호(outNo)·
    권리유형(selTypedesc)이 나온다. 상세는 목록과 다른 오퍼레이션이라 일일
    트래픽도 따로 잡힌다.
    """
    kept: dict[str, dict[str, dict]] = {"free": {}, "pay": {}}
    dropped: list[dict] = []
    calls = 0
    stopped = ""
    for kind, op in (("free", "getFreeTL"), ("pay", "getPayTL")):
        for kw in keywords:
            if calls >= max_calls:
                stopped = f"요청 상한 {max_calls}회 도달"
                break
            calls += 1
            try:
                xml = _get(op, key, {"pageNo": 1, "numOfRows": PER_PAGE,
                                     "sel_title": kw})
            except urllib.error.HTTPError as e:
                print(f"  ! [{op}/{kw}] HTTP {e.code}")
                continue
            except Exception as e:
                print(f"  ! [{op}/{kw}] {type(e).__name__}: {e}")
                continue
            msg = _err(xml)
            if msg:
                print(f"  ! [{op}/{kw}] {msg}")
                # 인증 오류는 모든 요청에서 같으니 계속 두드릴 이유가 없다.
                if "SERVICE_KEY" in msg or "returnReasonCode=30" in msg:
                    stopped = f"인증 오류로 중단 ({msg})"
                    break
                continue
            got = _items(xml)
            n_ok = 0
            for it in got:
                if _relevant(_title(it), kw):
                    it["_kw"] = kw
                    kept[kind].setdefault(_seq(it), it)
                    n_ok += 1
                else:
                    dropped.append({**it, "_kw": kw, "_kind": kind})
            print(f"  · {op} '{kw}': {len(got)}건 중 {n_ok}건 채택 "
                  f"(누적 {len(kept[kind])})")
            if DELAY:
                time.sleep(DELAY)
        if stopped:
            break

    if detail and not stopped:
        for kind, op in (("free", "getFreeTLDetail"), ("pay", "getPayTLDetail")):
            for seq, it in kept[kind].items():
                try:
                    xml = _get(op, key, {"sel_seq": seq})
                except Exception as e:
                    print(f"  ! [{op}/{seq}] {type(e).__name__}: {e}")
                    continue
                if _err(xml):
                    continue
                more = _items(xml)
                if more:
                    it.update({k: v for k, v in more[0].items() if v})
                if DELAY:
                    time.sleep(DELAY)
            print(f"  · {op}: {len(kept[kind])}건 상세 채움")

    return {
        "generated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "source": "공공데이터포털 · 지식재산처 특허기술거래 국유판매기술정보",
        "keywords": keywords,
        "calls": calls,
        "stopped": stopped,
        "free": list(kept["free"].values()),
        "pay": list(kept["pay"].values()),
        # 낱말 첫머리 규칙에 걸러진 것들. 버리지 않고 남겨 규칙을 다시 볼 근거로.
        "dropped": dropped,
    }


def main() -> int:
    key = os.getenv("DATA_GO_KR_KEY", "").strip()
    if not key:
        print("DATA_GO_KR_KEY 가 없습니다. 발급키를 환경변수로 넣고 다시 실행하세요.")
        return 1
    keywords = (os.getenv("STAOWN_KEYWORDS") or "").split() or DEFAULT_KEYWORDS
    max_calls = int(os.getenv("STAOWN_MAX_CALLS", "60"))
    # 저장소 루트에 둔다. site/ 는 빌드 산출물이라 .gitignore 에 있어 커밋되지 않는다
    # (brief.json·patent_brief.json 과 같은 자리 — 사람이 만들어 커밋하는 파일들).
    out = os.getenv("STAOWN_OUT") or "staown.json"

    print(f"검색어 {len(keywords)}개 · 요청 상한 {max_calls}회")
    data = collect(key, keywords, max_calls)
    if not data["free"] and not data["pay"]:
        print("\n한 건도 받지 못했습니다 — 저장하지 않습니다"
              + (f" ({data['stopped']})" if data["stopped"] else "") + ".")
        return 1

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n무상 {len(data['free'])}건 · 유상 {len(data['pay'])}건 "
          f"· 걸러냄 {len(data['dropped'])}건 (요청 {data['calls']}회) → {out}")
    sample = (data["free"] or data["pay"])[0]
    print("첫 항목 필드:", ", ".join(sample.keys()))
    if data["dropped"]:
        print("걸러낸 예:", "; ".join(_title(d) for d in data["dropped"][:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
