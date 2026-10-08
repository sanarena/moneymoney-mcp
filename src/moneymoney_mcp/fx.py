"""Foreign-exchange conversion based on ECB euro reference rates.

Rates are fetched from the ECB's daily XML feed and cached locally for 12
hours. Conversion is exact cross-multiplication through EUR; currency pairs
outside ECB coverage (about 30 currencies, e.g. no VND) return ``None`` so
callers can report them as unconverted instead of inventing a rate.
"""

from __future__ import annotations

import time

import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
CACHE_TTL_SECONDS = 12 * 3600


class FxError(Exception):
    """Exchange rates are unavailable."""


@dataclass(frozen=True)
class RateTable:
    rates: dict[str, float]  # currency -> units per 1 EUR
    as_of: str  # ECB reference date, YYYY-MM-DD
    stale: bool  # True when served from an expired cache after fetch failure


def cache_path() -> Path:
    return Path.home() / ".cache" / "moneymoney-mcp" / "ecb-daily.xml"


def fetch_rates_xml(timeout: float = 15) -> bytes:
    try:
        with urllib.request.urlopen(ECB_URL, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        raise FxError(f"Could not fetch ECB rates: {exc}") from exc


def parse_rates(payload: bytes) -> RateTable:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FxError(f"Could not parse ECB rates: {exc}") from exc
    rates: dict[str, float] = {}
    as_of = ""
    for node in root.iter():
        if "time" in node.attrib:
            as_of = node.attrib["time"]
        if "currency" in node.attrib and "rate" in node.attrib:
            try:
                rates[node.attrib["currency"].upper()] = float(node.attrib["rate"])
            except ValueError:
                continue
    if not rates:
        raise FxError("ECB feed contained no usable rates.")
    return RateTable(rates=rates, as_of=as_of, stale=False)


def get_rates() -> RateTable:
    """Return cached-or-fresh ECB rates, falling back to stale cache."""
    path = cache_path()
    cached: bytes | None = None
    fresh = False
    try:
        if path.is_file():
            cached = path.read_bytes()
            fresh = time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS
    except OSError:
        cached = None
    if fresh and cached:
        try:
            table = parse_rates(cached)
        except FxError:
            cached = None  # Corrupt cache: fall through to a fresh fetch.
        else:
            return RateTable(rates=table.rates, as_of=table.as_of, stale=False)
    try:
        payload = fetch_rates_xml()
        table = parse_rates(payload)
    except FxError:
        if cached:
            table = parse_rates(cached)
            return RateTable(rates=table.rates, as_of=table.as_of, stale=True)
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    except OSError:
        pass  # Cache is best effort; the fresh table is still returned.
    return table


def convert(
    amount: float, from_currency: str, to_currency: str, rates: dict[str, float]
) -> float | None:
    """Convert via EUR cross rates. Returns None when coverage is missing."""
    src, dst = from_currency.upper(), to_currency.upper()
    if src == dst:
        return amount
    if src != "EUR" and src not in rates:
        return None
    if dst != "EUR" and dst not in rates:
        return None
    in_eur = amount if src == "EUR" else amount / rates[src]
    return in_eur if dst == "EUR" else in_eur * rates[dst]
