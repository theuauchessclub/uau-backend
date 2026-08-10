#!/usr/bin/env python3
import csv
import difflib
import gzip
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"
OUT_XML = HERE / "us-entertainment.xml"
REPORT = HERE / "match-report.csv"
UNMATCHED = HERE / "unmatched.csv"
SUMMARY = HERE / "summary.json"

SOURCES = [
    ("us2", "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"),
    ("locals", "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz"),
    ("tubi", "https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/tubi-epg/tubi-us.xml"),
    ("prime", "https://raw.githubusercontent.com/theuauchessclub/uau-backend/main/primevideo-epg/primevideo-us.xml"),
]

ALIASES = {
    "AMERICA HEROES": ["AmericanHeroesChannel.us", "American Heroes Channel"],
    "BRAVO EAST": ["Bravo.us", "Bravo"],
    "CATCHY COMEDY": ["CatchyComedy.us", "Catchy Comedy"],
    "DISCOVERY SCIENCE": ["ScienceChannel.us", "Science Channel"],
    "DISCOVERY EAST": ["DiscoveryChannel.us", "Discovery Channel"],
    "DIY": ["MagnoliaNetwork.us", "Magnolia Network"],
    "TBS WEST": ["TBS.us", "TBS"],
    "FOX SOUL": ["FoxSoul.us", "FOX Soul"],
    "FXM": ["FXMovieChannel.us", "FXM"],
    "GAME SHOW CENTRAL": ["GameShowCentral.us", "Game Show Central"],
    "HD NET MOVIES": ["HDNetMovies.us", "HDNet Movies"],
    "ION": ["ION.us", "ION"],
    "ION MYSTERY": ["IONMystery.us", "ION Mystery"],
    "ION PLUS": ["IONPlus.us", "ION Plus"],
    "MY 9 WWOR NEW YORK": ["WWOR.us", "WWOR-TV", "My9"],
    "LIFETIME REAL WOMEN": ["LifetimeRealWomen.us", "Lifetime Real Women"],
    "LOCAL NOW": ["LocalNow.us", "Local Now"],
    "ME TV": ["MeTV.us", "MeTV"],
    "OWN": ["OprahWinfreyNetwork.us", "OWN"],
    "PBS": ["PBS.us", "PBS"],
    "AFV FAMILY": ["AFV Family"],
    "SHOWTIME BET": ["SHOxBET.us", "SHO x BET"],
    "SPECTRUM BAY NEWS 9": ["BayNews9.us", "Spectrum Bay News 9"],
    "TBS": ["TBS.us", "TBS"],
    "TCM": ["TCM.us", "Turner Classic Movies"],
    "TEEN NICK": ["TeenNick.us", "TeenNick"],
    "TLC": ["TLC.us", "TLC"],
    "TLC WEST": ["TLC.us", "TLC"],
    "TNT": ["TNT.us", "TNT"],
    "TNT EAST": ["TNT.us", "TNT"],
    "TVG": ["FanDuelRacing.us", "TVG"],
    "TVG 2": ["FanDuelTV.us", "TVG2"],
    "VICELAND": ["Vice.us", "VICE"],
    "A WEALTH OF ENTERTAINMENT AWE": ["AWE.us", "AWE"],
    "OVATION": ["Ovation.us", "Ovation"],
    "LAW AND CRIME": ["LawAndCrime.us", "Law & Crime"],
    "EL REY": ["ElReyNetwork.us", "El Rey"],
    "GREAT AMERICAN FAITH AND LIVING": ["GreatAmericanFaithAndLiving.us", "Great American Faith & Living"],
    "CIRCLE TV": ["Circle.us", "Circle"],
    "WORLD FISHING": ["WorldFishingNetwork.us", "World Fishing Network"],
    "KTLA LOS ANGELES": ["KTLA.us", "KTLA-DT.us_locals1", "KTLA"],
    "REAL AMERICAS VOICE": ["RealAmericasVoice.us", "Real America's Voice"],
}

def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")

def ascii_text(s):
    s = nfkc(s)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def clean_name(s, relaxed=False):
    s = ascii_text(s).upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"^(US|PRIME|TUBI)\s*:\s*", "", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    toks = [t for t in s.split() if t]
    toks = [t for t in toks if t not in {"HD","FHD","UHD","4K","3840P","60FPS","RAW"}]
    if relaxed:
        toks = [t for t in toks if t not in {"EAST","WEST","NETWORK","CHANNEL"}]
    return " ".join(toks)

def canon_id(name):
    return nfkc(name).strip()

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "UAU-EPG-Builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def build_source(name, url):
    root = fetch(url)
    channels = {}
    display_index = {}
    relaxed_index = {}
    id_lower = {}
    programmes = {}
    for ch in root.findall("channel"):
        cid = ch.get("id", "")
        if not cid:
            continue
        id_lower[cid.lower()] = cid
        names = [d.text or "" for d in ch.findall("display-name")]
        channels[cid] = ch
        for n in names + [cid]:
            k = clean_name(n, False)
            if k:
                display_index.setdefault(k, []).append(cid)
            rk = clean_name(n, True)
            if rk:
                relaxed_index.setdefault(rk, []).append(cid)
        programmes.setdefault(cid, [])
    for p in root.findall("programme"):
        cid = p.get("channel", "")
        if cid:
            programmes.setdefault(cid, []).append(p)
    return {"name": name, "url": url, "channels": channels, "id_lower": id_lower,
            "display": display_index, "relaxed": relaxed_index, "programmes": programmes}

def choose_candidate(item, sources):
    name = item["name"]
    provider_id = (item.get("provider_epg_id") or "").strip()
    if provider_id:
        for src in sources:
            cid = src["id_lower"].get(provider_id.lower())
            if cid:
                return src, cid, 100.0, "provider-id"

    exact = clean_name(name, False)
    relaxed = clean_name(name, True)
    for val in ALIASES.get(relaxed, []):
        for src in sources:
            cid = src["id_lower"].get(val.lower())
            if cid:
                return src, cid, 100.0, "alias-id"
            ids = src["display"].get(clean_name(val, False), [])
            if ids:
                return src, ids[0], 99.0, "alias-name"
            ids = src["relaxed"].get(clean_name(val, True), [])
            if ids:
                return src, ids[0], 98.0, "alias-relaxed"

    for src in sources:
        ids = src["display"].get(exact, [])
        if ids:
            return src, ids[0], 98.0, "name-exact"
    for src in sources:
        ids = src["relaxed"].get(relaxed, [])
        if ids:
            return src, ids[0], 96.0, "name-relaxed"

    best = None
    target_tokens = set(relaxed.split())
    if not target_tokens:
        return None
    for src in sources:
        for key, ids in src["relaxed"].items():
            other_tokens = set(key.split())
            overlap = len(target_tokens & other_tokens)
            if overlap == 0:
                continue
            ratio = difflib.SequenceMatcher(None, relaxed, key).ratio() * 100.0
            if ratio < 92.0:
                continue
            if len(target_tokens) >= 3 and overlap < 2:
                continue
            cand = (ratio, overlap, src, ids[0])
            if best is None or (ratio, overlap) > (best[0], best[1]):
                best = cand
    if best:
        return best[2], best[3], round(best[0], 1), "fuzzy"
    return None

def clone(elem):
    return ET.fromstring(ET.tostring(elem, encoding="utf-8"))

def main():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    sources = []
    source_status = []
    for name, url in SOURCES:
        try:
            src = build_source(name, url)
            sources.append(src)
            source_status.append({"source": name, "ok": True, "channels": len(src["channels"]),
                                  "programmes": sum(len(v) for v in src["programmes"].values())})
        except Exception as e:
            source_status.append({"source": name, "ok": False, "error": repr(e)})
    if not sources:
        raise RuntimeError("No EPG sources loaded")

    out = ET.Element("tv", {"generator-info-name": "UAU US Entertainment EPG",
                            "source-info-name": "Normalized US Entertainment guide for TiviMate"})
    rows, unmatched = [], []
    matched = programme_count = 0
    for item in catalog:
        result = choose_candidate(item, sources)
        if not result:
            unmatched.append({"name": item["name"], "provider_epg_id": item.get("provider_epg_id", "")})
            continue
        src, source_cid, score, method = result
        target_cid = canon_id(item["name"])
        src_ch = src["channels"].get(source_cid)
        ch = ET.Element("channel", {"id": target_cid})
        ET.SubElement(ch, "display-name").text = target_cid
        if src_ch is not None:
            icon = src_ch.find("icon")
            if icon is not None and icon.get("src"):
                ET.SubElement(ch, "icon", {"src": icon.get("src")})
        out.append(ch)
        pcount = 0
        for p in src["programmes"].get(source_cid, []):
            cp = clone(p)
            cp.set("channel", target_cid)
            out.append(cp)
            pcount += 1
        programme_count += pcount
        matched += 1
        rows.append({"name": item["name"], "provider_epg_id": item.get("provider_epg_id", ""),
                     "source": src["name"], "source_epg_id": source_cid, "score": score,
                     "method": method, "programmes": pcount})

    ET.indent(out, space="  ")
    ET.ElementTree(out).write(OUT_XML, encoding="utf-8", xml_declaration=True)
    with REPORT.open("w", newline="", encoding="utf-8") as f:
        fields = ["name","provider_epg_id","source","source_epg_id","score","method","programmes"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    with UNMATCHED.open("w", newline="", encoding="utf-8") as f:
        fields = ["name","provider_epg_id"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(unmatched)
    summary = {"catalog_channels": len(catalog), "matched_channels": matched,
               "unmatched_channels": len(unmatched),
               "match_rate_percent": round(100.0 * matched / len(catalog), 1),
               "programmes_written": programme_count, "source_status": source_status}
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
