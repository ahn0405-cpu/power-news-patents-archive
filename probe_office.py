"""[임시] OPS 에서 '공개 특허청' 필터 문법 확인 — 출원인×특허청 정확 집계용."""
from __future__ import annotations
import base64, json, time, urllib.parse, urllib.request
import patent_config as cfg

AUTH="https://ops.epo.org/3.2/auth/accesstoken"
SEARCH="https://ops.epo.org/3.2/rest-services/published-data/search/biblio"

def token():
    cred=base64.b64encode(f"{cfg.OPS_KEY}:{cfg.OPS_SECRET}".encode()).decode()
    r=urllib.request.Request(AUTH,data=b"grant_type=client_credentials",
        headers={"Authorization":"Basic "+cred,"Content-Type":"application/x-www-form-urlencoded"})
    with urllib.request.urlopen(r,timeout=40) as x: return json.loads(x.read())["access_token"]

def total(tk,cql):
    u=SEARCH+"?q="+urllib.parse.quote(cql)
    r=urllib.request.Request(u,headers={"Authorization":"Bearer "+tk,"Accept":"application/json","X-OPS-Range":"1-1"})
    with urllib.request.urlopen(r,timeout=40) as x: d=json.loads(x.read())
    return int(d["ops:world-patent-data"]["ops:biblio-search"]["@total-result-count"])

def main():
    tk=token()
    cpc=" or ".join(f'cpc="{c}"' for cat in cfg.CATEGORIES for c in cat["cpc"])
    base=f'pa="Samsung Electronics" and pd within "20260427 20260723" and ({cpc})'
    print("기준(특허청 무제한):", total(tk,base), "건\n")
    print("=== 특허청 필터 문법 후보 ===")
    for label,frag in [
        ('pn=US*',        'pn=US*'),
        ('pn any "US"',   'pn any "US"'),
        ('pn=US',         'pn=US'),
        ('ap=US',         'ap=US'),
        ('pn all "US"',   'pn all "US"'),
    ]:
        try:
            n=total(tk, base+" and "+frag); print(f"  {label:<16} → {n}건")
        except Exception as e:
            print(f"  {label:<16} → 실패 {e}")
        time.sleep(0.4)
    print("\n=== 동작하는 문법으로 국가별 분해(삼성) ===")
    for c in ["US","KR","CN","JP","EP","WO"]:
        for frag in [f'pn={c}*', f'pn any "{c}"']:
            try:
                n=total(tk, base+" and "+frag); print(f"  {c} ({frag}): {n}건"); break
            except Exception as e:
                pass
        time.sleep(0.4)

if __name__=="__main__": main()
