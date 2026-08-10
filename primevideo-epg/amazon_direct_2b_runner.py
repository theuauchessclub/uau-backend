#!/usr/bin/env python3
"""Run Pass 2B with a tolerant Prime Video clock parser."""
from datetime import datetime, timedelta
import re

import amazon_direct


def parse_clock(token: str, base_date, previous: datetime | None) -> datetime:
    value = re.sub(r"\s+", " ", token.strip()).upper()
    value = re.sub(r"(?<=\d)([AP]M)$", r" \1", value)
    tm = datetime.strptime(value, "%I:%M %p").time()
    candidate = datetime.combine(base_date, tm, amazon_direct.TZ)
    now = datetime.now(amazon_direct.TZ)
    if previous is None:
        if candidate < now - timedelta(hours=3):
            candidate += timedelta(days=1)
    else:
        while candidate <= previous:
            candidate += timedelta(days=1)
    return candidate


amazon_direct.parse_clock = parse_clock
amazon_direct.main()
