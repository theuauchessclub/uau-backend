#!/usr/bin/env python3
import csv
import gzip
import io
import json
import re
import shutil
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path

import requests
from lxml import etree
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent
CHANNEL_GLOB = "channels-*.json"
ALIASES_FILE = ROOT / "aliases.json"
OUT_XML = ROOT / "primevideo-us.xml"
OUT_GZ = ROOT / "primevideo-us.xml.gz"
MATCH_REPORT = ROOT / "match-report.csv"
UNMATCHED_REPORT = ROOT / "unmatched.csv"
SUMMARY_FILE = ROOT / "summary.json"

SOURCES = [
    {"key": "us2", "priority": 10, "ids": "https://epgshare01.online/epgshare01/epg_ripper_US2.txt", "xml": "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"},
    {"key": "locals", "priority": 20, "ids": "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.txt", "xml": "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz"},
    {"key": "sports", "priority": 30, "ids": "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.txt", "xml": "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz"},
    {"key": "plex", "priority": 40, "ids": "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.txt", "xml": "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz"},
    {"key": "peacock", "priority": 50, "ids": "https://epgshare01.online/epgshare01/epg_ripper_PEACOCK1.txt", "xml": "https://epgshare01.online/epgshare01/epg_ripper_PEACOCK1.xml.gz"},
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PrimeVideoEPG/1.0; +https://github.com/theuauchessclub/uau-backend)", "Accept": "*/*"}
DROP_WORDS = {"HD", "SD", "FHD", "UHD", "4K", "1080P", "720P", "2160P", "CHANNEL", "NETWORK", "TV", "THE", "RAW"}
SOURCE_SUFFIX_RE = re.compile(r"\.(?:us2|us_locals1|us_sports1|plex1|peacock1)$", re.I)


def nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "")


def strip_prime_name(name: str) -> str:
    value = nfkc(name)
    value = re.sub(r"^\s*PRIME\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\s+RAW\s*$", "", value, flags=re.I)
    return value.strip()


def normalize(value: str) -> str:
    value = nfkc(value)
    value = SOURCE_SUFFIX_RE.sub("", value)
    value = value.replace("&", " AND ").replace("+", " PLUS ")
    value = re.sub(r"\b5\s*STAR\s*MAX\b", " 5STARMAX ", value, flags=re.I)
    value = re.sub(r"\bHBO\s*MAX\b", " MAX ", value, flags=re.I)
    value = re.sub(r"\bMGM\s*PLUS\b", " MGM+ ", value, flags=re.I)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).upper()
    return " ".join(w for w in value.split() if w not in DROP_WORDS)


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize(value))


def fetch_text(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.text


def fetch_binary(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.content


def parse_id_list(text: str, source_key: str):
    ids = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("--") or re.fullmatch(r"\d{10,14}", line) or "." not in line:
            continue
        ids.append({"id": line, "source": source_key, "norm": normalize(line), "compact": compact(line)})
    return ids


def build_candidate_index():
    all_ids, source_status = [], []
    for source in SOURCES:
        try:
            ids = parse_id_list(fetch_text(source["ids"]), source["key"])
            all_ids.extend(ids)
            source_status.append({"source": source["key"], "ids": len(ids), "ok": True})
            print(f"{source['key']}: loaded {len(ids)} EPG IDs")
        except Exception as exc:
            source_status.append({"source": source["key"], "ids": 0, "ok": False, "error": str(exc)})
            print(f"WARNING: could not load {source['key']} ID list: {exc}", file=sys.stderr)
    if not all_ids:
        raise RuntimeError("No EPG source ID lists could be downloaded.")
    exact, compact_index = {}, {}
    for item in all_ids:
        exact.setdefault(item["norm"], []).append(item)
        compact_index.setdefault(item["compact"], []).append(item)
    return all_ids, exact, compact_index, source_status


def load_aliases():
    if not ALIASES_FILE.exists():
        return {}
    raw = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
    return {normalize(k): v for k, v in raw.items()}


def source_priority(source_key: str) -> int:
    for source in SOURCES:
        if source["key"] == source_key:
            return source["priority"]
    return 999


def choose_best(items):
    return sorted(items, key=lambda x: (source_priority(x["source"]), len(x["id"])))[0]


def find_alias_target(alias_target: str, candidates):
    target_lower = alias_target.lower()
    return next((item for item in candidates if item["id"].lower() == target_lower), None)


def match_channel(channel, candidates, exact, compact_index, aliases):
    raw_name = channel["name"]
    base = strip_prime_name(raw_name)
    norm, comp = normalize(base), compact(base)
    if norm in aliases:
        found = find_alias_target(aliases[norm], candidates)
        if found:
            return found, 100.0, "alias"
    callsign_match = re.search(r"\(([WK][A-Z]{2,4})(?:-[A-Z0-9]+)?\)", base.upper())
    if callsign_match:
        callsign = callsign_match.group(1)
        local_hits = [item for item in candidates if item["source"] == "locals" and re.match(rf"^{re.escape(callsign)}(?:-|\.|$)", item["id"], flags=re.I)]
        if local_hits:
            primary = [item for item in local_hits if re.match(rf"^{re.escape(callsign)}-DT\.", item["id"], flags=re.I)]
            return choose_best(primary or local_hits), 99.0, "callsign"
    if norm in exact:
        return choose_best(exact[norm]), 98.0, "exact"
    if comp in compact_index:
        return choose_best(compact_index[comp]), 97.0, "compact"
    choices = {f"{item['source']}::{item['id']}": item["norm"] for item in candidates}
    result = process.extractOne(norm, choices, scorer=fuzz.WRatio)
    if not result:
        return None, 0.0, "none"
    _, score, key = result
    source_key, epg_id = key.split("::", 1)
    found = next((i for i in candidates if i["source"] == source_key and i["id"] == epg_id), None)
    if found and score >= 92:
        return found, float(score), "fuzzy"
    return None, float(score), "unmatched"


def map_channels(channels):
    candidates, exact, compact_index, source_status = build_candidate_index()
    aliases = load_aliases()
    mapped, unmatched = [], []
    for channel in channels:
        item, score, method = match_channel(channel, candidates, exact, compact_index, aliases)
        if item:
            mapped.append({**channel, "source": item["source"], "epg_id": item["id"], "score": round(score, 1), "method": method, "base_name": strip_prime_name(channel["name"])})
        else:
            unmatched.append({**channel, "best_score": round(score, 1), "base_name": strip_prime_name(channel["name"])})
    return mapped, unmatched, source_status


def create_output_root(mapped):
    root = etree.Element("tv", attrib={"generator-info-name": "Prime Video EPG Builder", "generator-info-url": "https://github.com/theuauchessclub/uau-backend/tree/main/primevideo-epg"})
    for item in mapped:
        channel_el = etree.SubElement(root, "channel", id=item["name"])
        etree.SubElement(channel_el, "display-name").text = item["name"]
        etree.SubElement(channel_el, "display-name").text = item["base_name"]
        if item.get("icon"):
            etree.SubElement(channel_el, "icon", src=item["icon"])
    return root


def append_programmes(root, mapped):
    by_source = {}
    for item in mapped:
        by_source.setdefault(item["source"], {}).setdefault(item["epg_id"], []).append(item)
    programme_count, source_programmes = 0, {}
    for source in SOURCES:
        source_key, wanted = source["key"], by_source.get(source["key"])
        if not wanted:
            continue
        try:
            stream = gzip.GzipFile(fileobj=io.BytesIO(fetch_binary(source["xml"])))
            source_programmes[source_key] = 0
            for _, elem in etree.iterparse(stream, events=("end",), tag=("programme",)):
                targets = wanted.get(elem.get("channel"))
                if targets:
                    for target in targets:
                        copied = deepcopy(elem)
                        copied.set("channel", target["name"])
                        root.append(copied)
                        programme_count += 1
                        source_programmes[source_key] += 1
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
            print(f"{source_key}: copied {source_programmes[source_key]} programmes")
        except Exception as exc:
            print(f"WARNING: could not process {source_key} XML: {exc}", file=sys.stderr)
            source_programmes[source_key] = -1
    return programme_count, source_programmes


def write_reports(mapped, unmatched, source_status, programme_count, source_programmes):
    with MATCH_REPORT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "base_name", "stream_id", "source", "epg_id", "score", "method"])
        writer.writeheader()
        for row in mapped:
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    with UNMATCHED_REPORT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "base_name", "stream_id", "best_score"])
        writer.writeheader()
        for row in unmatched:
            writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
    summary = {"channels_total": len(mapped) + len(unmatched), "channels_matched": len(mapped), "channels_unmatched": len(unmatched), "match_rate_percent": round((len(mapped) / max(1, len(mapped) + len(unmatched))) * 100, 1), "programmes_written": programme_count, "source_status": source_status, "source_programmes": source_programmes}
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


def main():
    channel_files = sorted(ROOT.glob(CHANNEL_GLOB))
    if not channel_files:
        raise RuntimeError(f"No {CHANNEL_GLOB} files found.")
    channels = []
    for channel_file in channel_files:
        payload = json.loads(channel_file.read_text(encoding="utf-8"))
        for item in payload:
            channels.append({"name": item, "stream_id": "", "icon": ""} if isinstance(item, str) else item)
    mapped, unmatched, source_status = map_channels(channels)
    root = create_output_root(mapped)
    programme_count, source_programmes = append_programmes(root, mapped)
    etree.ElementTree(root).write(str(OUT_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    with OUT_XML.open("rb") as src, gzip.open(OUT_GZ, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    write_reports(mapped, unmatched, source_status, programme_count, source_programmes)
    if programme_count == 0:
        raise RuntimeError("No programmes were written. Refusing to publish an empty guide.")


if __name__ == "__main__":
    main()
