#!/usr/bin/env python3
import csv
import gzip
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"
OUT_XML = HERE / "us-movies.xml"
REPORT = HERE / "match-report.csv"
UNMATCHED = HERE / "unmatched.csv"
SUMMARY = HERE / "summary.json"

SOURCE_URL = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"

def nfkc(s):
    return unicodedata.normalize("NFKC", s or "").strip()

def clone(elem):
    return ET.fromstring(ET.tostring(elem, encoding="utf-8"))

def fetch_xml(url):
    req = urllib.request.Request(url, headers={"User-Agent": "UAU-US-Movies-EPG/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def clean_placeholder_title(name):
    s = nfkc(name)
    s = re.sub(r"^US:\s*", "", s, flags=re.I)
    s = re.sub(r"\s+(HD|4K|FHD|UHD|HDAR)$", "", s, flags=re.I)
    return s.strip()

def xmltv_time(dt):
    return dt.strftime("%Y%m%d%H%M%S +0000")

def main():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    src = fetch_xml(SOURCE_URL)

    channels = {}
    programmes = {}
    for ch in src.findall("channel"):
        cid = ch.get("id", "")
        if cid:
            channels[cid] = ch
    for p in src.findall("programme"):
        cid = p.get("channel", "")
        if cid:
            programmes.setdefault(cid, []).append(p)

    out = ET.Element("tv", {
        "generator-info-name": "UAU US Movies EPG",
        "source-info-name": "EPGShare US2 + explicit placeholders for unmatched provider channels"
    })

    rows = []
    unmatched = []
    matched = 0
    programme_count = 0
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    placeholder_stop = now + timedelta(days=8)

    for item in catalog:
        target_id = nfkc(item["name"])
        source_id = (item.get("source_epg_id") or "").strip()

        ch = ET.Element("channel", {"id": target_id})
        ET.SubElement(ch, "display-name").text = target_id

        if source_id and source_id in channels:
            src_ch = channels[source_id]
            icon = src_ch.find("icon")
            if icon is not None and icon.get("src"):
                ET.SubElement(ch, "icon", {"src": icon.get("src")})
            out.append(ch)

            pcount = 0
            for p in programmes.get(source_id, []):
                cp = clone(p)
                cp.set("channel", target_id)
                out.append(cp)
                pcount += 1

            rows.append({
                "name": item["name"],
                "source_epg_id": source_id,
                "status": "real-schedule",
                "programmes": pcount
            })
            matched += 1
            programme_count += pcount
        else:
            out.append(ch)
            cur = now
            pcount = 0
            title = clean_placeholder_title(item["name"])
            while cur < placeholder_stop:
                stop = min(cur + timedelta(hours=6), placeholder_stop)
                p = ET.Element("programme", {
                    "channel": target_id,
                    "start": xmltv_time(cur),
                    "stop": xmltv_time(stop),
                })
                ET.SubElement(p, "title").text = title
                ET.SubElement(p, "desc").text = (
                    "Channel placeholder: no reliable public programme-level schedule "
                    "was found for this provider stream."
                )
                ET.SubElement(p, "category").text = "Movies"
                out.append(p)
                pcount += 1
                cur = stop
            rows.append({
                "name": item["name"],
                "source_epg_id": source_id,
                "status": "placeholder",
                "programmes": pcount
            })
            unmatched.append({"name": item["name"], "provider_epg_id": item.get("provider_epg_id", "")})
            programme_count += pcount

    ET.indent(out, space="  ")
    ET.ElementTree(out).write(OUT_XML, encoding="utf-8", xml_declaration=True)

    with REPORT.open("w", newline="", encoding="utf-8") as f:
        fields = ["name", "source_epg_id", "status", "programmes"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    with UNMATCHED.open("w", newline="", encoding="utf-8") as f:
        fields = ["name", "provider_epg_id"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(unmatched)

    summary = {
        "catalog_channels": len(catalog),
        "real_schedule_channels": matched,
        "placeholder_channels": len(unmatched),
        "real_schedule_percent": round(100.0 * matched / len(catalog), 1),
        "programmes_written": programme_count,
        "source_url": SOURCE_URL,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
