#!/usr/bin/env python3
"""Remove Amazon-direct channels that cannot safely map to a PRIME channel.

The downstream merger has fuzzy matching for useful naming variants. This guard
prevents visually similar but semantically different names (especially numbers)
from ever reaching that fuzzy stage.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from lxml import etree

ROOT = Path(__file__).resolve().parent
AMAZON_XML = ROOT / "amazon-direct.xml"
REPORT = ROOT / "amazon-direct-guard-report.json"
DROP_WORDS = {"HD", "SD", "FHD", "UHD", "4K", "1080P", "720P", "2160P", "CHANNEL", "NETWORK", "TV", "THE", "RAW"}


def strip_prime(name: str) -> str:
    value = unicodedata.normalize("NFKC", name or "")
    value = re.sub(r"^\s*PRIME\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"\s+RAW\s*$", "", value, flags=re.I)
    return value.strip()


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("&", " AND ").replace("+", " PLUS ")
    value = re.sub(r"\bHBO\s*MAX\b", " MAX ", value, flags=re.I)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).upper()
    return " ".join(w for w in value.split() if w not in DROP_WORDS)


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm(value))


def numbers(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+", unicodedata.normalize("NFKC", value or "")))


def load_prime_names() -> list[str]:
    names = []
    for path in sorted(ROOT.glob("channels-*.json")):
        for item in json.loads(path.read_text(encoding="utf-8")):
            names.append(item if isinstance(item, str) else item.get("name", ""))
    return [n for n in names if n]


def main() -> None:
    prime = load_prime_names()
    norm_set = {norm(strip_prime(n)) for n in prime}
    compact_set = {compact(strip_prime(n)) for n in prime}
    tree = etree.parse(str(AMAZON_XML))
    root = tree.getroot()
    channel_names = {ch.get("id"): (ch.findtext("display-name") or "") for ch in root.findall("channel")}
    keep_ids = set()
    removed = []
    for cid, display in channel_names.items():
        n, c = norm(display), compact(display)
        safe = n in norm_set or c in compact_set
        if safe:
            keep_ids.add(cid)
        else:
            removed.append(display)
    for ch in list(root.findall("channel")):
        if ch.get("id") not in keep_ids:
            root.remove(ch)
    for p in list(root.findall("programme")):
        if p.get("channel") not in keep_ids:
            root.remove(p)
    tree.write(str(AMAZON_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    result = {
        "amazon_channels_before": len(channel_names),
        "amazon_channels_kept_for_safe_merge": len(keep_ids),
        "amazon_channels_removed_from_fuzzy_merge": len(removed),
        "removed": sorted(removed),
    }
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
