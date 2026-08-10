#!/usr/bin/env python3
"""Pass 3: match remaining PRIME channels against additional FAST/linear EPG sources.

Runs only against channels still unmatched after the base EPG and Amazon-direct pass.
Amazon-direct and existing exact mappings are never overwritten.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path

import requests
from lxml import etree
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent
OUT_XML = ROOT / "primevideo-us.xml"
MATCH_REPORT = ROOT / "match-report.csv"
UNMATCHED_REPORT = ROOT / "unmatched.csv"
SUMMARY = ROOT / "summary.json"
REPORT = ROOT / "pass3-report.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PrimeVideoEPG/3.0)", "Accept": "*/*"}
DROP_WORDS = {"HD", "SD", "FHD", "UHD", "4K", "1080P", "720P", "2160P", "CHANNEL", "NETWORK", "RAW"}

SOURCES = [
    ("distro", "https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz"),
    ("fanduel", "https://epgshare01.online/epgshare01/epg_ripper_FANDUEL1.xml.gz"),
    ("rakuten", "https://epgshare01.online/epgshare01/epg_ripper_RAKUTEN1.xml.gz"),
    ("tbnplus", "https://epgshare01.online/epgshare01/epg_ripper_TBNPLUS1.xml.gz"),
    ("whaletv", "https://epgshare01.online/epgshare01/epg_ripper_WHALETVPLUS1.xml.gz"),
    ("uk", "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"),
    ("canada", "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz"),
    ("australia", "https://epgshare01.online/epgshare01/epg_ripper_AU1.xml.gz"),
    ("mexico", "https://epgshare01.online/epgshare01/epg_ripper_MX1.xml.gz"),
    ("pluto", "https://i.mjh.nz/PlutoTV/us.xml.gz"),
    ("samsung", "https://i.mjh.nz/SamsungTVPlus/us.xml.gz"),
    ("roku", "https://i.mjh.nz/Roku/all.xml.gz"),
    ("plex_mjh", "https://i.mjh.nz/Plex/us.xml.gz"),
]


def strip_prime(name: str) -> str:
    value = unicodedata.normalize("NFKC", name or "")
    value = re.sub(r"^\s*PRIME\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\s+RAW\s*$", "", value, flags=re.I)
    return value.strip()


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("&", " AND ").replace("+", " PLUS ")
    value = re.sub(r"\bHBO\s*MAX\b", " MAX ", value, flags=re.I)
    value = re.sub(r"\bLIVE\s+NOW\b", " LIVENOW ", value, flags=re.I)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).upper()
    words = [w for w in value.split() if w not in DROP_WORDS]
    return " ".join(words)


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize(value))


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def fetch_source(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return r.content


def gzip_stream(data: bytes):
    return gzip.GzipFile(fileobj=io.BytesIO(data))


def source_channels(data: bytes, source: str) -> list[dict]:
    result = []
    stream = gzip_stream(data)
    for _, elem in etree.iterparse(stream, events=("end",), tag="channel"):
        cid = elem.get("id", "")
        names = [x.text.strip() for x in elem.findall("display-name") if x.text and x.text.strip()]
        if cid:
            if names:
                for display in names[:3]:
                    result.append({"source": source, "id": cid, "display": display, "norm": normalize(display), "compact": compact(display)})
            # Also use the XMLTV id itself as a fallback alias when human-readable.
            if re.search(r"[A-Za-z]", cid):
                result.append({"source": source, "id": cid, "display": cid, "norm": normalize(cid), "compact": compact(cid)})
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    # dedupe source/id/norm
    dedup = {}
    for row in result:
        if row["norm"]:
            dedup[(row["source"], row["id"], row["norm"])] = row
    return list(dedup.values())


def score_candidate(target_norm: str, candidate_norm: str) -> float:
    wr = fuzz.WRatio(target_norm, candidate_norm)
    ts = fuzz.token_sort_ratio(target_norm, candidate_norm)
    return max(wr, ts)


def acceptable(target_norm: str, cand_norm: str, score: float) -> bool:
    # Short/generic channel names are dangerous; require exact-like matching.
    if len(target_norm.replace(" ", "")) <= 5:
        return score >= 99
    if score >= 97:
        return True
    # 94-96 only when most tokens overlap and neither side is tiny.
    if score >= 94:
        ta, ca = set(target_norm.split()), set(cand_norm.split())
        overlap = len(ta & ca) / max(1, min(len(ta), len(ca)))
        return overlap >= 0.75
    return False


def main() -> None:
    unmatched = read_csv(UNMATCHED_REPORT)
    match_rows = read_csv(MATCH_REPORT)
    if not unmatched:
        print("No unmatched channels remain; Pass 3 has nothing to do.")
        return

    payloads: dict[str, bytes] = {}
    candidates: list[dict] = []
    source_status = []
    for source, url in SOURCES:
        try:
            data = fetch_source(url)
            rows = source_channels(data, source)
            payloads[source] = data
            candidates.extend(rows)
            source_status.append({"source": source, "ok": True, "candidate_names": len(rows), "bytes": len(data)})
            print(f"Pass3 {source}: {len(rows)} candidate names")
        except Exception as exc:  # noqa: BLE001
            source_status.append({"source": source, "ok": False, "error": str(exc)})
            print(f"WARNING: Pass3 source {source} failed: {exc}")

    exact: dict[str, list[int]] = {}
    compact_idx: dict[str, list[int]] = {}
    norm_choices = []
    for idx, row in enumerate(candidates):
        exact.setdefault(row["norm"], []).append(idx)
        compact_idx.setdefault(row["compact"], []).append(idx)
        norm_choices.append(row["norm"])

    selected = []
    used_source_ids = set()
    still_unmatched = []
    for row in unmatched:
        name = row.get("name", "")
        base = strip_prime(name)
        n, c = normalize(base), compact(base)
        hit = None
        score = 0.0
        method = ""
        indices = exact.get(n, [])
        if indices:
            # Prefer first unused exact candidate.
            for idx in indices:
                cand = candidates[idx]
                if (cand["source"], cand["id"]) not in used_source_ids:
                    hit, score, method = cand, 100.0, "pass3-exact"
                    break
        if hit is None:
            indices = compact_idx.get(c, [])
            for idx in indices:
                cand = candidates[idx]
                if (cand["source"], cand["id"]) not in used_source_ids:
                    hit, score, method = cand, 99.0, "pass3-compact"
                    break
        if hit is None and n:
            # Ask RapidFuzz for several candidates so a duplicate/unusable top hit does not block us.
            for _, raw_score, idx in process.extract(n, norm_choices, scorer=fuzz.WRatio, limit=8):
                cand = candidates[idx]
                s = score_candidate(n, cand["norm"])
                if (cand["source"], cand["id"]) in used_source_ids:
                    continue
                if acceptable(n, cand["norm"], s):
                    hit, score, method = cand, s, "pass3-fuzzy"
                    break
        if hit:
            used_source_ids.add((hit["source"], hit["id"]))
            selected.append({
                "name": name,
                "base_name": base,
                "stream_id": row.get("stream_id", ""),
                "source": hit["source"],
                "epg_id": hit["id"],
                "source_display": hit["display"],
                "score": round(float(score), 1),
                "method": method,
            })
        else:
            still_unmatched.append(row)

    if not selected:
        report = {"new_matches": 0, "remaining_unmatched": len(still_unmatched), "source_status": source_status}
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    tree = etree.parse(str(OUT_XML))
    root = tree.getroot()
    existing_channel_ids = {c.get("id") for c in root.findall("channel")}
    selected_by_source: dict[str, dict[str, list[dict]]] = {}
    for m in selected:
        selected_by_source.setdefault(m["source"], {}).setdefault(m["epg_id"], []).append(m)
        if m["name"] not in existing_channel_ids:
            ch = etree.Element("channel", id=m["name"])
            etree.SubElement(ch, "display-name").text = m["name"]
            etree.SubElement(ch, "display-name").text = m["base_name"]
            first_prog = root.find("programme")
            if first_prog is None:
                root.append(ch)
            else:
                root.insert(root.index(first_prog), ch)
            existing_channel_ids.add(m["name"])

    programmes_added = 0
    matches_with_programmes = set()
    source_programmes = {}
    for source, wanted in selected_by_source.items():
        data = payloads.get(source)
        if not data:
            continue
        count = 0
        stream = gzip_stream(data)
        for _, elem in etree.iterparse(stream, events=("end",), tag="programme"):
            targets = wanted.get(elem.get("channel", ""))
            if targets:
                for target in targets:
                    cp = deepcopy(elem)
                    cp.set("channel", target["name"])
                    root.append(cp)
                    count += 1
                    programmes_added += 1
                    matches_with_programmes.add(target["name"])
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        source_programmes[source] = count

    # Keep only mappings that actually produced schedule rows.
    successful = [m for m in selected if m["name"] in matches_with_programmes]
    failed_selected_names = {m["name"] for m in selected if m["name"] not in matches_with_programmes}
    if failed_selected_names:
        # Remove declarations created for candidates with no programmes.
        for ch in list(root.findall("channel")):
            if ch.get("id") in failed_selected_names and not any(p.get("channel") == ch.get("id") for p in root.findall("programme")):
                root.remove(ch)

    tree.write(str(OUT_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)

    match_by_name = {r.get("name"): r for r in match_rows}
    for m in successful:
        match_by_name[m["name"]] = {
            "name": m["name"], "base_name": m["base_name"], "stream_id": m.get("stream_id", ""),
            "source": f"pass3_{m['source']}", "epg_id": m["epg_id"], "score": m["score"], "method": m["method"],
        }
    updated_matches = sorted(match_by_name.values(), key=lambda r: r.get("name", ""))
    write_csv(MATCH_REPORT, updated_matches, ["name", "base_name", "stream_id", "source", "epg_id", "score", "method"])

    success_names = {m["name"] for m in successful}
    remaining = [r for r in unmatched if r.get("name") not in success_names]
    write_csv(UNMATCHED_REPORT, remaining, ["name", "base_name", "stream_id", "best_score"])

    summary = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
    total = int(summary.get("channels_total", len(updated_matches) + len(remaining)))
    summary.update({
        "channels_matched": len(updated_matches),
        "channels_unmatched": max(0, total - len(updated_matches)),
        "match_rate_percent": round(len(updated_matches) / max(1, total) * 100, 1),
        "programmes_written": len(root.findall("programme")),
        "pass3_new_channels": len(successful),
        "pass3_programmes": programmes_added,
    })
    summary.setdefault("source_programmes", {}).update({f"pass3_{k}": v for k, v in source_programmes.items()})
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    report = {
        "starting_unmatched": len(unmatched),
        "candidate_names_loaded": len(candidates),
        "provisional_matches": len(selected),
        "new_matches_with_programmes": len(successful),
        "remaining_unmatched": len(remaining),
        "combined_match_rate_percent": summary["match_rate_percent"],
        "programmes_added": programmes_added,
        "source_programmes": source_programmes,
        "source_status": source_status,
        "matches": successful,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "matches"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
