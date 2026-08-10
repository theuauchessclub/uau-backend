#!/usr/bin/env python3
"""Merge direct Prime Video Live TV schedules into the supplemental XMLTV guide."""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path

from lxml import etree
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent
CHANNEL_GLOB = "channels-*.json"
AMAZON_XML = ROOT / "amazon-direct.xml"
AMAZON_REPORT = ROOT / "amazon-direct-report.json"
OUT_XML = ROOT / "primevideo-us.xml"
MATCH_REPORT = ROOT / "match-report.csv"
UNMATCHED_REPORT = ROOT / "unmatched.csv"
SUMMARY = ROOT / "summary.json"
PASS2_REPORT = ROOT / "pass2-report.json"

DROP_WORDS = {"HD", "SD", "FHD", "UHD", "4K", "1080P", "720P", "2160P", "CHANNEL", "NETWORK", "TV", "THE", "RAW"}


def strip_prime(name: str) -> str:
    value = unicodedata.normalize("NFKC", name or "")
    value = re.sub(r"^\s*PRIME\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\s+RAW\s*$", "", value, flags=re.I)
    return value.strip()


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("&", " AND ").replace("+", " PLUS ")
    value = re.sub(r"\bHBO\s*MAX\b", " MAX ", value, flags=re.I)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).upper()
    return " ".join(w for w in value.split() if w not in DROP_WORDS)


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize(value))


def load_prime_channels() -> list[dict]:
    rows = []
    for path in sorted(ROOT.glob(CHANNEL_GLOB)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload:
            row = {"name": item, "stream_id": "", "icon": ""} if isinstance(item, str) else item
            rows.append(row)
    return rows


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def main() -> None:
    if not AMAZON_XML.exists() or not OUT_XML.exists():
        raise RuntimeError("Pass 2 merge requires amazon-direct.xml and the base primevideo-us.xml")

    prime_rows = load_prime_channels()
    prime_by_name = {r["name"]: r for r in prime_rows}
    exact: dict[str, list[str]] = {}
    compact_idx: dict[str, list[str]] = {}
    choice_norms: dict[str, str] = {}
    for row in prime_rows:
        base = strip_prime(row["name"])
        n, c = normalize(base), compact(base)
        exact.setdefault(n, []).append(row["name"])
        compact_idx.setdefault(c, []).append(row["name"])
        choice_norms[row["name"]] = n

    amazon_tree = etree.parse(str(AMAZON_XML))
    amazon_root = amazon_tree.getroot()
    amazon_names = {}
    for ch in amazon_root.findall("channel"):
        display = ch.findtext("display-name") or ch.get("id", "").replace("amazon::", "")
        amazon_names[ch.get("id")] = display

    by_amazon_channel: dict[str, list] = {}
    for p in amazon_root.findall("programme"):
        by_amazon_channel.setdefault(p.get("channel"), []).append(p)

    used_targets: set[str] = set()
    direct_matches = []
    rejected = []
    for amazon_id, amazon_name in amazon_names.items():
        n, c = normalize(amazon_name), compact(amazon_name)
        target = None
        score = 0.0
        method = ""
        if n in exact and len(exact[n]) == 1:
            target, score, method = exact[n][0], 100.0, "amazon-exact"
        elif c in compact_idx and len(compact_idx[c]) == 1:
            target, score, method = compact_idx[c][0], 99.0, "amazon-compact"
        else:
            result = process.extractOne(n, choice_norms, scorer=fuzz.WRatio)
            if result:
                _, score, target = result
                if score >= 94:
                    method = "amazon-fuzzy"
                else:
                    target = None
        if target and target not in used_targets and by_amazon_channel.get(amazon_id):
            used_targets.add(target)
            direct_matches.append({
                "amazon_id": amazon_id,
                "amazon_name": amazon_name,
                "target": target,
                "score": round(float(score), 1),
                "method": method,
                "programmes": len(by_amazon_channel[amazon_id]),
            })
        else:
            rejected.append({"amazon_name": amazon_name, "best_score": round(float(score), 1), "target": target or ""})

    out_tree = etree.parse(str(OUT_XML))
    out_root = out_tree.getroot()
    existing_channels = {ch.get("id"): ch for ch in out_root.findall("channel")}
    direct_targets = {m["target"] for m in direct_matches}

    # Prime Video direct data wins for directly matched channels.
    for p in list(out_root.findall("programme")):
        if p.get("channel") in direct_targets:
            out_root.remove(p)

    added_channels = 0
    direct_programmes = 0
    for match in direct_matches:
        target = match["target"]
        if target not in existing_channels:
            row = prime_by_name[target]
            ch = etree.Element("channel", id=target)
            etree.SubElement(ch, "display-name").text = target
            etree.SubElement(ch, "display-name").text = strip_prime(target)
            if row.get("icon"):
                etree.SubElement(ch, "icon", src=row["icon"])
            # Keep channel declarations before programme rows.
            first_programme = out_root.find("programme")
            if first_programme is None:
                out_root.append(ch)
            else:
                out_root.insert(out_root.index(first_programme), ch)
            existing_channels[target] = ch
            added_channels += 1
        for src in by_amazon_channel[match["amazon_id"]]:
            p = deepcopy(src)
            p.set("channel", target)
            out_root.append(p)
            direct_programmes += 1

    out_tree.write(str(OUT_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)

    match_rows = read_csv(MATCH_REPORT)
    match_by_name = {r.get("name"): r for r in match_rows}
    for m in direct_matches:
        row = prime_by_name[m["target"]]
        match_by_name[m["target"]] = {
            "name": m["target"],
            "base_name": strip_prime(m["target"]),
            "stream_id": row.get("stream_id", ""),
            "source": "amazon_direct",
            "epg_id": m["amazon_id"],
            "score": m["score"],
            "method": m["method"],
        }
    updated_match_rows = sorted(match_by_name.values(), key=lambda r: r.get("name", ""))
    write_csv(MATCH_REPORT, updated_match_rows, ["name", "base_name", "stream_id", "source", "epg_id", "score", "method"])

    unmatched_rows = [r for r in read_csv(UNMATCHED_REPORT) if r.get("name") not in direct_targets]
    write_csv(UNMATCHED_REPORT, unmatched_rows, ["name", "base_name", "stream_id", "best_score"])

    summary = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
    total = len(prime_rows)
    matched_total = len(updated_match_rows)
    programme_total = len(out_root.findall("programme"))
    summary.update({
        "channels_total": total,
        "channels_matched": matched_total,
        "channels_unmatched": total - matched_total,
        "match_rate_percent": round(matched_total / max(1, total) * 100, 1),
        "programmes_written": programme_total,
        "amazon_direct_channels": len(direct_matches),
        "amazon_direct_programmes": direct_programmes,
    })
    summary.setdefault("source_programmes", {})["amazon_direct"] = direct_programmes
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    collector = json.loads(AMAZON_REPORT.read_text(encoding="utf-8")) if AMAZON_REPORT.exists() else {}
    pass2 = {
        "collector": collector,
        "direct_matches": len(direct_matches),
        "direct_programmes": direct_programmes,
        "new_channel_declarations": added_channels,
        "rejected_amazon_channels": rejected,
        "matches": direct_matches,
        "combined_match_rate_percent": summary["match_rate_percent"],
    }
    PASS2_REPORT.write_text(json.dumps(pass2, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in pass2.items() if k not in {"matches", "rejected_amazon_channels"}}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
