#!/usr/bin/env python3
import csv, difflib, gzip, json, re, unicodedata, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / 'catalog.txt'
OUT_XML = HERE / 'directv-go.xml'
REPORT = HERE / 'match-report.csv'
UNMATCHED = HERE / 'unmatched.csv'
SUMMARY = HERE / 'summary.json'

SOURCES = [
 ('us2','https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz'),
 ('locals','https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz'),
 ('usent','https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/us-entertainment-epg/us-entertainment.xml'),
 ('tubi','https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/tubi-epg/tubi-us.xml'),
 ('prime','https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/primevideo-epg/primevideo-us.xml'),
 ('pluto','https://i.mjh.nz/PlutoTV/us.xml.gz'),
 ('samsung','https://i.mjh.nz/SamsungTVPlus/us.xml.gz'),
 ('plex','https://i.mjh.nz/Plex/us.xml.gz'),
 ('roku','https://i.mjh.nz/Roku/all.xml.gz'),
]

ALIASES = {
 'E': ['E!.Entertainment.Television.HD.us2'],
 'ESPN2': ['ESPN2.HD.us2'], 'ESPNEWS': ['ESPNEWS.HD.us2'], 'ESPNU': ['ESPNU.HD.us2'],
 'ESPN8 THE OCHO': ['ESPN8.The.Ocho.us2'],
 'DISNEY': ['Disney.Channel.HD.us2'], 'DISNEY JUNIOR': ['Disney.Junior.HD.us2'],
 'DISCOVERY': ['Discovery.Channel.HD.us2'], 'DISCOVERY FAMILY': ['Discovery.Family.Channel.HD.us2'],
 'FOX BUSINESS': ['Fox.Business.HD.us2'], 'FREEFORM': ['Freeform.HD.us2'],
 'FX MOVIE': ['FX.Movie.Channel.HD.us2'], 'HALLMARK': ['Hallmark.Channel.HD.us2'],
 'HBO': ['HBO.East.us2'], 'HBO2': ['HBO2.HD.us2'],
 'NATIONAL GEOGRAPHIC': ['National.Geographic.HD.us2'], 'NAT GEO WILD': ['National.Geographic.Wild.HD.us2'],
 'NICK JR': ['Nick.Jr.HD.us2'], 'NICKELODEON': ['Nickelodeon.HD.us2'],
 'OWN': ['Oprah.Winfrey.Network.HD.us2'], 'PARAMOUNT': ['Paramount.Network.HD.us2'],
 'POP': ['POP.HD.us2'], 'REELZCHANNEL': ['ReelzChannel.HD.us2'],
 'SCIENCE': ['Science.Channel.HD.us2'], 'SPORTSNET NEW YORK 639': ['SNY.SportsNet.New.York.HD.us2'],
 'THE WEATHER': ['The.Weather.Channel.HD.us2'], 'UP': ['UPtv.us2'],
 'TV ONE': ['TV.ONE.HD.us2'], 'UNIMAS': ['UniMas.us2'], 'UNIVERSO': ['UNIVERSO.HD.us2'],
 'UNIVISION': ['Univision.Network.HD.us2'], 'VH1': ['VH1.HD.us2'], 'VICE': ['Vice.HD.us2'],
 'WILLOW SPORTS': ['Willow.Cricket.HD.us2'],
 'YANKEES ENTERTAINMENT AND SPORTS': ['Yes.Network.us2'], 'YES': ['Yes.Network.us2'],
 'TBS': ['TBS.HD.us2'], 'TCM': ['Turner.Classic.Movies.HD.us2'], 'TLC': ['TLC.HD.(US).us2'],
 'SYFY': ['Syfy.HD.us2'], 'TEENNICK': ['Teen.Nick.us2'], 'TUDN': ['TUDN.us2'],
 'BIG TEN': ['Big.Ten.Network.HD.us2'], 'NBA': ['NBA.TV.HD.us2'], 'NHL': ['NHL.Network.HD.us2'],
 'NEWSMAX': ['Newsmax.TV.HD.us2'], 'GREAT AMERICAN FAMILY': ['Great.American.Family.HD.us2'],
 'HOME SHOPPING': ['HSN.Home.Shopping.Network.HD.us2'], 'INVESTIGATION DISCOVERY': ['Investigation.Discovery.HD.us2'],
 'MAGNOLIA': ['Magnolia.Network.HD.us2'], 'MTV': ['MTV.-.Music.Television.HD.us2'],
 'MTV2': ['MTV2:.Music.Television.HD.us2'], 'MTV CLASSIC': ['MTV.Classic.us2'],
 'OXYGEN TRUE CRIME': ['Oxygen.True.Crime.HD.us2'], 'PUREFLIX': ['Pure.Flix.TV.us2'],
 'QVC2': ['QVC2.us2'], 'QVC3': ['QVC3.us2'], 'RACER': ['RACER.Network.HD.us2'],
 'SCRIPPS NEWS': ['Scripps.News.us2'], 'SHORTS': ['Shorts.TV.us2'],
 'SPORTSGRID': ['SportsGrid.us2'], 'SPORTSMAN': ['Sportsman.Channel.us2'],
 'STARZ ENCORE': ['Starz.Encore.HD.us2'], 'STARZ ENCORE SUSPENSE': ['Starz.Encore.Suspense.us2'],
 'SUNDANCE': ['SundanceTV.HD.us2'], 'TENNIS': ['Tennis.Channel.HD.us2'],
}

def nfkc(s): return unicodedata.normalize('NFKC', s or '')
def ascii_text(s):
 s=unicodedata.normalize('NFKD',nfkc(s)); return ''.join(c for c in s if not unicodedata.combining(c))
def clean(s, relaxed=False):
 s=ascii_text(s).upper().replace('&',' AND ')
 s=re.sub(r'^(GO|US|PRIME|TUBI)\s*:\s*','',s)
 s=re.sub(r'\([^)]*\)',' ',s)
 s=re.sub(r'[^A-Z0-9]+',' ',s)
 toks=[t for t in s.split() if t not in {'HD','FHD','UHD','4K','3840P','60FPS','RAW','US2','US','US_LOCALS1','PLEX'}]
 if relaxed: toks=[t for t in toks if t not in {'EAST','WEST','PACIFIC','NETWORK','CHANNEL','TV','STREAM','THE'}]
 return ' '.join(toks)
def clone(e): return ET.fromstring(ET.tostring(e,encoding='utf-8'))
def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':'UAU-DirectTV-EPG/1.0'})
 with urllib.request.urlopen(req,timeout=180) as r: raw=r.read()
 if url.endswith('.gz'): raw=gzip.decompress(raw)
 return ET.fromstring(raw)
def build_source(name,url):
 root=fetch(url); channels={}; ids={}; exact={}; relaxed={}; programmes={}
 for c in root.findall('channel'):
  cid=c.get('id','')
  if not cid: continue
  channels[cid]=c; ids[cid.lower()]=cid; programmes.setdefault(cid,[])
  vals=[cid]+[(d.text or '') for d in c.findall('display-name')]
  for v in vals:
   k=clean(v); rk=clean(v,True)
   if k: exact.setdefault(k,[]).append(cid)
   if rk: relaxed.setdefault(rk,[]).append(cid)
 for p in root.findall('programme'):
  cid=p.get('channel','')
  if cid: programmes.setdefault(cid,[]).append(p)
 return {'name':name,'channels':channels,'ids':ids,'exact':exact,'relaxed':relaxed,'programmes':programmes}
def alias_values(base_relaxed):
 out=[]
 for k,vals in ALIASES.items():
  if base_relaxed==k or base_relaxed.startswith(k+' '): out.extend(vals)
 return out
def choose(name,sources):
 base=clean(name); rel=clean(name,True)
 # NYC/local call signs in this DirecTV group.
 m=re.search(r'(?:^|\s)([WK][A-Z]{3})(?:$|\s)',base)
 if m:
  cs=m.group(1)
  for src in sources:
   if src['name']!='locals': continue
   for guess in (f'{cs}-DT.us_locals1',f'{cs}-DT_.us_locals1',f'{cs}.us_locals1'):
    cid=src['ids'].get(guess.lower())
    if cid: return src,cid,100.0,'callsign'
 for val in alias_values(rel):
  for src in sources:
   cid=src['ids'].get(val.lower())
   if cid: return src,cid,100.0,'alias-id'
   q=src['exact'].get(clean(val),[])
   if q: return src,q[0],99.0,'alias-name'
 for src in sources:
  q=src['exact'].get(base,[])
  if q: return src,q[0],98.0,'exact'
 for src in sources:
  q=src['relaxed'].get(rel,[])
  if q: return src,q[0],96.0,'relaxed'
 # Conservative fuzzy matching, mainly for FAST-channel naming variants.
 if len(rel)<4: return None
 best=None; target=set(rel.split())
 for src in sources:
  for key,cids in src['relaxed'].items():
   other=set(key.split()); overlap=len(target & other)
   if not overlap: continue
   ratio=difflib.SequenceMatcher(None,rel,key).ratio()*100
   min_ratio=94.0 if len(target)<=2 else 92.0
   if ratio<min_ratio: continue
   if len(target)>=3 and overlap<2: continue
   cand=(ratio,overlap,src,cids[0])
   if best is None or (ratio,overlap)>(best[0],best[1]): best=cand
 if best: return best[2],best[3],round(best[0],1),'fuzzy'
 return None

def main():
 names=[x.strip() for x in CATALOG.read_text(encoding='utf-8').splitlines() if x.strip()]
 sources=[]; status=[]
 for n,u in SOURCES:
  try:
   s=build_source(n,u); sources.append(s); status.append({'source':n,'ok':True,'channels':len(s['channels']),'programmes':sum(map(len,s['programmes'].values()))})
  except Exception as e: status.append({'source':n,'ok':False,'error':repr(e)})
 if not sources: raise RuntimeError('No EPG sources loaded')
 out=ET.Element('tv',{'generator-info-name':'UAU DirecTV GO EPG','source-info-name':'Normalized DirecTV GO guide for TiviMate'})
 rows=[]; unmatched=[]; totalp=0
 for name in names:
  r=choose(name,sources)
  if not r: unmatched.append({'name':name}); continue
  src,cid,score,method=r; target=nfkc(name).strip(); sc=src['channels'][cid]
  c=ET.Element('channel',{'id':target}); ET.SubElement(c,'display-name').text=target
  icon=sc.find('icon')
  if icon is not None and icon.get('src'): ET.SubElement(c,'icon',{'src':icon.get('src')})
  out.append(c); pc=0
  for p in src['programmes'].get(cid,[]):
   cp=clone(p); cp.set('channel',target); out.append(cp); pc+=1
  totalp+=pc; rows.append({'name':name,'source':src['name'],'source_epg_id':cid,'score':score,'method':method,'programmes':pc})
 ET.indent(out,space='  '); ET.ElementTree(out).write(OUT_XML,encoding='utf-8',xml_declaration=True)
 with REPORT.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=['name','source','source_epg_id','score','method','programmes']); w.writeheader(); w.writerows(rows)
 with UNMATCHED.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=['name']); w.writeheader(); w.writerows(unmatched)
 summary={'catalog_channels':len(names),'matched_channels':len(rows),'unmatched_channels':len(unmatched),'match_rate_percent':round(100*len(rows)/len(names),1),'programmes_written':totalp,'source_status':status}
 SUMMARY.write_text(json.dumps(summary,indent=2),encoding='utf-8'); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
