#!/usr/bin/env python3
"""Collect schedule data directly from the public US Prime Video Live TV page.

This intentionally uses only the public, unauthenticated guide page. Amazon can change
its markup at any time, so the collector is conservative: it only publishes programme
rows when it can identify a channel plus two consecutive explicit clock times.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from lxml import etree

ROOT = Path(__file__).resolve().parent
OUT_XML = ROOT / "amazon-direct.xml"
OUT_REPORT = ROOT / "amazon-direct-report.json"
DEBUG_HTML = ROOT / "amazon-direct-debug.html"

URLS = [
    "https://www.primevideo.com/livetv?tr=us&language=en_US",
    "https://www.primevideo.com/livetv?language=en_US&tr=us",
]
TZ = ZoneInfo("America/New_York")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIME_RE = re.compile(r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AP]M)\b", re.I)
CHANNEL_ALT_RE = re.compile(r"^(.*?)\s+(?:Channel|channel|Canal|canal|Cha[iî]ne|canal|频道|チャンネル)$", re.I)
NOISE_RE = re.compile(
    r"^(?:watch live|live now|currently airing|on now|scroll|prime|image|more|remaining|"
    r"\d+\s*(?:min|mins|minutes|hr|hrs|hours)\s*(?:remaining|left)?|"
    r"season\s*\d+|episode\s*\d+|tv-[a-z0-9-]+|\d+\+)$",
    re.I,
)


def fetch_page() -> tuple[str, str]:
    last_error = None
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.set("lc-main", "en_US", domain="www.primevideo.com")
    for url in URLS:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            if len(r.text) > 20_000:
                return r.text, r.url
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"Prime Video Live TV page could not be downloaded: {last_error}")


def clean_channel_alt(alt: str) -> str | None:
    alt = re.sub(r"\s+", " ", (alt or "").strip())
    m = CHANNEL_ALT_RE.match(alt)
    return m.group(1).strip() if m else None


def find_schedule_container(img) -> object | None:
    """Find the smallest ancestor around a channel image that contains guide times."""
    node = img
    best = None
    for _ in range(12):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = node.get_text("\n", strip=True)
        times = list(TIME_RE.finditer(text))
        if len(times) >= 2 and len(text) <= 30_000:
            best = node
            break
    return best


def meaningful_lines(segment: str, channel_name: str) -> list[str]:
    lines = []
    channel_lower = channel_name.casefold()
    for raw in segment.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -|•\t")
        if not line or len(line) > 220:
            continue
        low = line.casefold()
        if low == channel_lower or low == f"{channel_lower} channel":
            continue
        if TIME_RE.fullmatch(line) or NOISE_RE.match(line):
            continue
        if "remaining" in low or "currently airing" in low or "watch live" in low:
            continue
        if line not in lines:
            lines.append(line)
    return lines


def parse_clock(token: str, base_date, previous: datetime | None) -> datetime:
    tm = datetime.strptime(re.sub(r"\s+", " ", token.strip()).upper(), "%I:%M %p").time()
    candidate = datetime.combine(base_date, tm, TZ)
    now = datetime.now(TZ)
    if previous is None:
        # The explicit rows on the page are normally current/upcoming. If the clock
        # time is far behind now, treat it as tomorrow.
        if candidate < now - timedelta(hours=3):
            candidate += timedelta(days=1)
    else:
        while candidate <= previous:
            candidate += timedelta(days=1)
    return candidate


def extract_programmes(channel_name: str, container) -> list[dict]:
    text = container.get_text("\n", strip=True)
    matches = list(TIME_RE.finditer(text))
    if len(matches) < 2:
        return []

    starts: list[datetime] = []
    entries: list[tuple[datetime, list[str]]] = []
    previous = None
    today = datetime.now(TZ).date()
    for i, match in enumerate(matches):
        start = parse_clock(match.group(0), today, previous)
        previous = start
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[match.end():end_pos]
        lines = meaningful_lines(segment, channel_name)
        starts.append(start)
        entries.append((start, lines))

    programmes = []
    for i in range(len(entries) - 1):
        start, lines = entries[i]
        stop = entries[i + 1][0]
        if not lines or stop <= start or stop - start > timedelta(hours=6):
            continue
        title = lines[0]
        subtitle = lines[1] if len(lines) > 1 and len(lines[1]) <= 140 else ""
        programmes.append({
            "channel": channel_name,
            "start": start,
            "stop": stop,
            "title": title,
            "subtitle": subtitle,
        })
    return programmes


def fmt_xmltv(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def main() -> None:
    report = {
        "source": "Prime Video public Live TV page",
        "collected_at": datetime.now(TZ).isoformat(),
        "page_url": None,
        "http_ok": False,
        "channel_images_found": 0,
        "channels_with_schedule": 0,
        "programmes": 0,
        "channels": [],
        "error": None,
    }
    root = etree.Element("tv", attrib={"generator-info-name": "Prime Video Direct Collector"})
    try:
        html, final_url = fetch_page()
        report["page_url"] = final_url
        report["http_ok"] = True
        # Keep the raw response only for troubleshooting in Actions; workflow removes it
        # before commit unless explicitly requested.
        DEBUG_HTML.write_text(html, encoding="utf-8")
        soup = BeautifulSoup(html, "lxml")
        seen_channels: set[str] = set()
        channel_programmes: dict[str, list[dict]] = {}

        for img in soup.find_all("img", alt=True):
            channel = clean_channel_alt(img.get("alt", ""))
            if not channel:
                continue
            report["channel_images_found"] += 1
            if channel in seen_channels:
                continue
            seen_channels.add(channel)
            container = find_schedule_container(img)
            if not container:
                continue
            programmes = extract_programmes(channel, container)
            if programmes:
                channel_programmes[channel] = programmes

        # Fallback: Amazon sometimes exposes channel labels in aria-label rather than img alt.
        if not channel_programmes:
            for node in soup.find_all(attrs={"aria-label": True}):
                channel = clean_channel_alt(node.get("aria-label", ""))
                if not channel or channel in seen_channels:
                    continue
                seen_channels.add(channel)
                container = find_schedule_container(node)
                if not container:
                    continue
                programmes = extract_programmes(channel, container)
                if programmes:
                    channel_programmes[channel] = programmes

        for channel in sorted(channel_programmes):
            ch = etree.SubElement(root, "channel", id=f"amazon::{channel}")
            etree.SubElement(ch, "display-name").text = channel
            for item in channel_programmes[channel]:
                p = etree.SubElement(root, "programme", channel=f"amazon::{channel}", start=fmt_xmltv(item["start"]), stop=fmt_xmltv(item["stop"]))
                etree.SubElement(p, "title", lang="en").text = item["title"]
                if item["subtitle"]:
                    etree.SubElement(p, "sub-title", lang="en").text = item["subtitle"]

        report["channels_with_schedule"] = len(channel_programmes)
        report["programmes"] = sum(len(v) for v in channel_programmes.values())
        report["channels"] = sorted(channel_programmes)
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        print(f"WARNING: Amazon direct collection failed: {exc}")

    etree.ElementTree(root).write(str(OUT_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    OUT_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
