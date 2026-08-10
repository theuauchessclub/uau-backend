#!/usr/bin/env python3
import csv, difflib, json, re, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
CATALOG=HERE/"catalog.json"
OUT=HERE/"peacock-raw.xml"
REPORT=HERE/"match-report.csv"
UNMATCHED=HERE/"unmatched.csv"
SUMMARY=HERE/"summary.json"

ALIASES={
"WWE NETWORK":["WWE Network"],"BOUNCE":["Bounce TV","Bounce"],"BOUNCE TV":["Bounce TV","Bounce"],
"CIRCLE COUNTRY":["Circle Country"],"CONFESS BY NOSEY":["Confess by Nosey","Confess"],
"COURT TV":["Court TV"],"COURT TV LEGENDARY TRIALS":["Court TV: Legendary Trials","Court TV Legendary Trials"],
"DATELINE 24 7":["Dateline 24/7"],"EBONY TV BY LIONSGATE":["Ebony TV by Lionsgate","Ebony TV"],
"FAMILY FEUD STEVE HARVEY":["Family Feud Steve Harvey","Family Feud"],"GAME SHOW CENTRAL":["Game Show Central"],
"GHOST HUNTERS":["Ghost Hunters Channel","Ghost Hunters"],"ION":["ION"],"ION MYSTERY":["ION Mystery"],
"LAW AND CRIME":["Law&Crime","Law & Crime Network","Law&Crime Network"],"LOL":["LOL! Network","LOL Network"],
"LOVE NATURE":["Love Nature"],"MOVIESPHERE BY LIONSGATE":["MovieSphere by Lionsgate","MovieSphere"],
"NBC BOSTON":["NBC 10 Boston News","NBC Boston News","NBC10 Boston"],"NBC CHICAGO":["NBC Chicago News","NBC 5 Chicago News"],
"NBC LOS ANGELES":["NBC Los Angeles News","NBC 4 Los Angeles News"],"NBC LX HOME":["NBC LX Home","LX Home"],
"NBC NEW YORK":["NBC New York News","NBC 4 New York News"],"NBC NEWS NOW":["NBC News NOW","NBC News Now"],
"NBC PHILADELPHIA":["NBC Philadelphia News","NBC10 Philadelphia News"],"NBC SOUTH FLORIDA":["NBC South Florida News","NBC 6 South Florida News"],
"NBC SPORTS NOW":["NBC Sports NOW","NBC Sports Now"],"NEWS 12 NEW YORK":["News 12 New York","News 12"],
"NFL":["NFL Channel"],"NOSEY":["Nosey"],"NOTICIAS TELEMUNDO AHORA":["Noticias Telemundo Ahora"],
"PREMIER LEAGUE":["Premier League TV"],"REELZ":["REELZ","Reelz"],"SCRIPPS":["Scripps News"],
"SKY":["Sky News"],"SNL VAULT":["SNL Vault","Saturday Night Live"],"TEAM USA":["Team USA TV","Team USA"],
"TELEMUNDO AL DIA":["Telemundo Al Día","Telemundo Al Dia"],"TELEMUNDO DEPORTES AHORA":["Telemundo Deportes Ahora"],
"THAT 70S SHOW":["That '70s Show","That 70s Show"],"THIS OLD HOUSE":["This Old House"],
"TODAY ALL DAY":["TODAY All Day","Today All Day"],"TOP GEAR":["Top Gear"],"WNBC":["WNBC","NBC New York News","NBC 4 New York"]
}

def nfkc(s): return unicodedata.normalize("NFKC",s or "")
def clean(s,rel=False):
    s=unicodedata.normalize("NFKD",nfkc(s).upper())
    s="".join(c for c in s if not unicodedata.combining(c)).replace("&"," AND ")
    s=re.sub(r"^US\s*:\s*","",s); s=re.sub(r"\bRAW\b"," ",s); s=re.sub(r"[^A-Z0-9]+"," ",s)
    t=[x for x in s.split() if x not in {"HD","FHD","UHD","4K","3840P","60FPS"}]
    if rel: t=[x for x in t if x not in {"TV","NETWORK","CHANNEL","NEWS"}]
    return " ".join(t)

def get_guide():
    now=datetime.now(timezone.utc)-timedelta(hours=1); now=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0)
    q=urllib.parse.urlencode({"startTime":now.strftime("%Y-%m-%dT%H:%M+00:00"),"assetsPerChannelCapLimit":"1000","contentSegments":"D2C,Free"})
    url="https://bff-ext.clients.peacocktv.com/bff/channel_guide?"+q
    h={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
       "X-SkyOTT-Device":"COMPUTER","X-SkyOTT-Language":"en","X-SkyOTT-Platform":"PC","X-SkyOTT-Proposition":"NBCUOTT","X-SkyOTT-Territory":"US","Accept":"application/json"}
    with urllib.request.urlopen(urllib.request.Request(url,headers=h),timeout=120) as r: data=json.load(r)
    ch={}
    for x in data.get("channels",[]):
        cid=str(x.get("id") or x.get("serviceKey") or x.get("name") or "")
        if cid: ch[cid]=x
    return ch,url

def index(ch):
    ex,rl={},{}
    for cid,x in ch.items():
        for n in [x.get("name",""),cid,x.get("gracenoteId","")]:
            a,b=clean(n),clean(n,True)
            if a: ex.setdefault(a,[]).append(cid)
            if b: rl.setdefault(b,[]).append(cid)
    return ex,rl

def choose(name,ch,ex,rl):
    a,b=clean(name),clean(name,True)
    aliases=ALIASES.get(b,[])+ALIASES.get(a,[])
    for z in aliases:
        za,zb=clean(z),clean(z,True)
        if za in ex:return ex[za][0],100,"alias-exact"
        if zb in rl:return rl[zb][0],99,"alias-relaxed"
    if a in ex:return ex[a][0],99,"name-exact"
    if b in rl:return rl[b][0],98,"name-relaxed"
    best=None
    for cid,x in ch.items():
        score=difflib.SequenceMatcher(None,b,clean(x.get("name",""),True)).ratio()*100
        if score>=86 and (best is None or score>best[0]):best=(score,cid)
    return (best[1],round(best[0],1),"fuzzy") if best else None

def xt(t): return datetime.fromtimestamp(int(t),timezone.utc).strftime("%Y%m%d%H%M%S +0000")

def main():
    cat=json.loads(CATALOG.read_text(encoding="utf-8")); channels,url=get_guide(); ex,rl=index(channels)
    tv=ET.Element("tv",{"generator-info-name":"UAU Peacock RAW EPG","source-info-name":"Official Peacock channel guide normalized for TiviMate"})
    rows=[]; miss=[]; pcount=0
    for item in cat:
        m=choose(item["name"],channels,ex,rl)
        if not m: miss.append({"name":item["name"]}); continue
        cid,score,method=m; src=channels[cid]; tid=nfkc(item["name"]).strip()
        c=ET.SubElement(tv,"channel",{"id":tid}); ET.SubElement(c,"display-name").text=tid
        logo=((src.get("logo") or {}).get("Default") or "").replace("{width}","360").replace("{height}","270")
        if logo: ET.SubElement(c,"icon",{"src":logo})
        n=0
        for ev in src.get("scheduleItems") or []:
            try: st=int(ev.get("startTimeUTC")); dur=int(ev.get("durationSeconds") or 0)
            except: continue
            if dur<=0: continue
            p=ET.SubElement(tv,"programme",{"channel":tid,"start":xt(st),"stop":xt(st+dur)})
            d=ev.get("data") or {}; ET.SubElement(p,"title").text=str(d.get("title") or "Peacock")
            if d.get("description"): ET.SubElement(p,"desc").text=str(d["description"])
            ET.SubElement(p,"category").text="Peacock"
            n+=1
        pcount+=n; rows.append({"name":item["name"],"source_name":src.get("name",""),"source_id":cid,"score":score,"method":method,"programmes":n})
    ET.indent(tv,space="  "); ET.ElementTree(tv).write(OUT,encoding="utf-8",xml_declaration=True)
    with REPORT.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["name","source_name","source_id","score","method","programmes"]); w.writeheader(); w.writerows(rows)
    with UNMATCHED.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["name"]); w.writeheader(); w.writerows(miss)
    s={"catalog_channels":len(cat),"peacock_source_channels":len(channels),"matched_channels":len(rows),"unmatched_channels":len(miss),
       "match_rate_percent":round(100*len(rows)/len(cat),1),"programmes_written":pcount,"source_url":url}
    SUMMARY.write_text(json.dumps(s,indent=2),encoding="utf-8"); print(json.dumps(s,indent=2))
if __name__=="__main__":main()
