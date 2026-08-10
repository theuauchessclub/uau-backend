#!/usr/bin/env python3
import copy
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_XML = ROOT / "tubi-us.xml"
OUT_SUMMARY = ROOT / "summary.json"
SOURCE_EPG = "https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml"

def canonical(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").upper()
    value = value.replace("&", " AND ")
    value = value.replace("+", " PLUS ")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def epg_id(display_name: str) -> str:
    return "TUBI:" + canonical(display_name)

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "UAU-Tubi-EPG/1.0"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()

def main():
    source_root = ET.fromstring(fetch(SOURCE_EPG))
    source_channels = {}
    for ch in source_root.findall("channel"):
        names = [x.text.strip() for x in ch.findall("display-name") if x.text and x.text.strip()]
        if names:
            source_channels[ch.get("id", "")] = (ch, names[0])

    programmes = {}
    for p in source_root.findall("programme"):
        programmes.setdefault(p.get("channel", ""), []).append(p)

    out = ET.Element("tv", {
        "generator-info-name": "UAU Tubi EPG",
        "source-info-name": "Tubi guide normalized for TiviMate"
    })

    used_ids = set()
    channel_map = {}
    written_programmes = 0

    for source_id, (source_channel, name) in source_channels.items():
        new_id = epg_id(name)
        if new_id in used_ids:
            continue
        used_ids.add(new_id)
        channel_map[source_id] = new_id

        ch = ET.SubElement(out, "channel", {"id": new_id})
        ET.SubElement(ch, "display-name").text = new_id
        for child in source_channel:
            if child.tag != "display-name":
                ch.append(copy.deepcopy(child))

    for source_id, new_id in channel_map.items():
        for p in programmes.get(source_id, []):
            cloned = copy.deepcopy(p)
            cloned.set("channel", new_id)
            out.append(cloned)
            written_programmes += 1

    ET.indent(out, space="  ")
    ET.ElementTree(out).write(OUT_XML, encoding="utf-8", xml_declaration=True)

    summary = {
        "source_channels": len(source_channels),
        "normalized_channels": len(channel_map),
        "programmes_written": written_programmes,
        "source_epg": SOURCE_EPG
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
