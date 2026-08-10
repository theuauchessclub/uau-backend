#!/usr/bin/env python3
from __future__ import annotations
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "summary.json"
MATCH = ROOT / "match-report.csv"
UNMATCHED = ROOT / "unmatched.csv"


def load_catalog_names():
    names = []
    for path in sorted(ROOT.glob("channels-*.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            names.append(item if isinstance(item, str) else item.get("name", ""))
    return [n for n in names if n]


def csv_names(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [r.get("name", "") for r in csv.DictReader(fh) if r.get("name")]


def main():
    catalog = load_catalog_names()
    catalog_unique = set(catalog)
    matched = set(csv_names(MATCH))
    unmatched_rows = csv_names(UNMATCHED)
    unmatched_unique = set(unmatched_rows)
    covered_entries = sum(1 for n in catalog if n in matched)
    unresolved_unique = catalog_unique - matched
    summary = json.loads(SUMMARY.read_text(encoding="utf-8")) if SUMMARY.exists() else {}
    summary.update({
        "catalog_entries_total": len(catalog),
        "catalog_unique_channels": len(catalog_unique),
        "duplicate_catalog_entries": len(catalog) - len(catalog_unique),
        "matched_unique_channels": len(matched & catalog_unique),
        "unmatched_unique_channels": len(unresolved_unique),
        "unique_match_rate_percent": round(len(matched & catalog_unique) / max(1, len(catalog_unique)) * 100, 1),
        "covered_catalog_entries": covered_entries,
        "entry_coverage_percent": round(covered_entries / max(1, len(catalog)) * 100, 1),
        "unmatched_report_rows": len(unmatched_rows),
        "unmatched_report_unique_names": len(unmatched_unique),
    })
    # Keep legacy fields aligned to unique-channel coverage so they no longer overstate misses due to duplicates.
    summary["channels_total"] = len(catalog_unique)
    summary["channels_matched"] = len(matched & catalog_unique)
    summary["channels_unmatched"] = len(unresolved_unique)
    summary["match_rate_percent"] = summary["unique_match_rate_percent"]
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
