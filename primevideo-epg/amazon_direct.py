#!/usr/bin/env python3
"""Collect public US Prime Video Live TV schedules, including pagination links.

Pass 2B follows server-rendered Prime Video Live TV pagination/service-token links
found on each public page. It remains unauthenticated and conservative: only rows
with explicit consecutive clock times are emitted.
"""
from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
OUT_XML = ROOT / "amazon-direct.xml"
OUT_REPORT = ROOT / "amazon-direct-report.json"
DEBUG_HTML = ROOT / "amazon-direct-debug.html"
SEED_URL = "https://www.primevideo.com/livetv?tr=us&language=en_US"
MAX_PAGES = 40
TZ = ZoneInfo("America/New_York")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIME_RE = re.compile(r"\b(1[0-2]|0?[1-9]):([0-5]\d)\s*([AP]M)\b", re.I)
CHANNEL_ALT_RE = re.compile(r"^(.*?)\s+(?:Channel|channel|Canal|canal|Cha[iî]ne|频道|チャンネル)$", re.I)
NOISE_RE = re.compile(
    r"^(?:watch live|live now|currently airing|on now|scroll|prime|image|more|remaining|"
    r"\d+\s*(?:min|mins|minutes|hr|hrs|hours)\s*(?:remaining|left)?|"
    r"season\s*\d+|episode\s*\d+|tv-[a-z0-9-]+|\d+\+)$",
    re.I,
)


def canonical_url(url: str) -> str:
    return url.split("#", 1)[0]


def is_pagination_url(url: str) -> bool:
    p = urlparse(url)
    if p.netloc not in {"www.primevideo.com", "primevideo.com"}:
        return False
    if "/livetv" not in p.path:
        return False
    q = p.query.lower()
    return any(k in q for k in ("servicetoken=", "startindex=", "pagesize=", "page="))


def clean_channel_alt(alt: str) -> str | None:
    alt = re.sub(r"\s+", " ", (alt or "").strip())
    m = CHANNEL_ALT_RE.match(alt)
    return m.group(1).strip() if m else None


def find_schedule_container(node) -> object | None:
    cur = node
    for _ in range(12):
        cur = getattr(cur, "parent", None)
        if cur is None:
            break
        text = cur.get_text("\n", strip=True)
        if len(TIME_RE.findall(text)) >= 2 and len(text) <= 30000:
            return cur
    return None


def meaningful_lines(segment: str, channel_name: str) -> list[str]:
    lines = []
    channel_lower = channel_name.casefold()
    for raw in segment.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -|•\t")
        if not line or len(line) > 220:
            continue
        low = line.casefold()
        if low in {channel_lower, f"{channel_lower} channel"}:
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
    entries = []
    previous = None
    today = datetime.now(TZ).date()
    for i, match in enumerate(matches):
        start = parse_clock(match.group(0), today, previous)
        previous = start
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lines = meaningful_lines(text[match.end():end_pos], channel_name)
        entries.append((start, lines))
    programmes = []
    for i in range(len(entries) - 1):
        start, lines = entries[i]
        stop = entries[i + 1][0]
        if not lines or stop <= start or stop - start > timedelta(hours=6):
            continue
        programmes.append({
            "channel": channel_name,
            "start": start,
            "stop": stop,
            "title": lines[0],
            "subtitle": lines[1] if len(lines) > 1 and len(lines[1]) <= 140 else "",
        })
    return programmes


def fmt_xmltv(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def programme_key(item: dict) -> tuple:
    return (item["start"].isoformat(), item["stop"].isoformat(), item["title"], item.get("subtitle", ""))


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.cookies.set("lc-main", "en_US", domain="www.primevideo.com")
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def main() -> None:
    report = {
        "source": "Prime Video public Live TV pages (Pass 2B pagination crawl)",
        "collected_at": datetime.now(TZ).isoformat(),
        "seed_url": SEED_URL,
        "http_ok": False,
        "pages_fetched": 0,
        "pagination_links_discovered": 0,
        "channel_images_found": 0,
        "channels_with_schedule": 0,
        "programmes": 0,
        "channels": [],
        "page_urls": [],
        "errors": [],
    }
    root = etree.Element("tv", attrib={"generator-info-name": "Prime Video Direct Collector Pass 2B"})
    session = build_session()
    queue = deque([SEED_URL])
    queued = {canonical_url(SEED_URL)}
    seen = set()
    channel_programmes: dict[str, dict[tuple, dict]] = {}
    first_html = None

    while queue and len(seen) < MAX_PAGES:
        url = canonical_url(queue.popleft())
        if url in seen:
            continue
        seen.add(url)
        try:
            r = session.get(url, timeout=(15, 60))
            r.raise_for_status()
            if len(r.text) < 10000:
                raise RuntimeError(f"response too small: {len(r.text)} bytes")
            report["http_ok"] = True
            report["pages_fetched"] += 1
            report["page_urls"].append(r.url)
            if first_html is None:
                first_html = r.text
            soup = BeautifulSoup(r.text, "lxml")

            for a in soup.find_all("a", href=True):
                full = canonical_url(urljoin(r.url, a.get("href", "")))
                if is_pagination_url(full) and full not in queued and full not in seen:
                    queued.add(full)
                    queue.append(full)
            report["pagination_links_discovered"] = max(0, len(queued) - 1)

            nodes = list(soup.find_all("img", alt=True)) + list(soup.find_all(attrs={"aria-label": True}))
            per_page_seen = set()
            for node in nodes:
                label = node.get("alt", "") or node.get("aria-label", "")
                channel = clean_channel_alt(label)
                if not channel or channel in per_page_seen:
                    continue
                per_page_seen.add(channel)
                report["channel_images_found"] += 1
                container = find_schedule_container(node)
                if not container:
                    continue
                progs = extract_programmes(channel, container)
                if not progs:
                    continue
                bucket = channel_programmes.setdefault(channel, {})
                for item in progs:
                    bucket[programme_key(item)] = item
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"url": url, "error": str(exc)})

    if first_html:
        DEBUG_HTML.write_text(first_html, encoding="utf-8")

    for channel in sorted(channel_programmes):
        ch = etree.SubElement(root, "channel", id=f"amazon::{channel}")
        etree.SubElement(ch, "display-name").text = channel
        for item in sorted(channel_programmes[channel].values(), key=lambda x: x["start"]):
            p = etree.SubElement(root, "programme", channel=f"amazon::{channel}", start=fmt_xmltv(item["start"]), stop=fmt_xmltv(item["stop"]))
            etree.SubElement(p, "title", lang="en").text = item["title"]
            if item["subtitle"]:
                etree.SubElement(p, "sub-title", lang="en").text = item["subtitle"]

    report["channels_with_schedule"] = len(channel_programmes)
    report["programmes"] = sum(len(v) for v in channel_programmes.values())
    report["channels"] = sorted(channel_programmes)
    etree.ElementTree(root).write(str(OUT_XML), encoding="UTF-8", xml_declaration=True, pretty_print=False)
    OUT_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"channels", "page_urls"}}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
