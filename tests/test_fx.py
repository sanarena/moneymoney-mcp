"""Tests for ECB exchange-rate parsing, conversion and caching."""

from __future__ import annotations

import os
import time

import pytest

from moneymoney_mcp import fx

SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-10-06">
      <Cube currency="USD" rate="1.1000"/>
      <Cube currency="JPY" rate="165.00"/>
      <Cube currency="MYR" rate="4.8000"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


def test_parse_rates_extracts_table():
    table = fx.parse_rates(SAMPLE_XML)
    assert table.rates == {"USD": 1.1, "JPY": 165.0, "MYR": 4.8}
    assert table.as_of == "2026-10-06"
    assert table.stale is False


def test_parse_rates_rejects_garbage():
    with pytest.raises(fx.FxError):
        fx.parse_rates(b"<html>nope</html>")


def test_convert_cross_and_identity():
    rates = {"USD": 1.1, "JPY": 165.0}
    assert fx.convert(110, "USD", "EUR", rates) == pytest.approx(100.0)
    assert fx.convert(100, "EUR", "JPY", rates) == pytest.approx(16500.0)
    assert fx.convert(110, "USD", "JPY", rates) == pytest.approx(16500.0)
    assert fx.convert(5, "VND", "VND", rates) == 5


def test_convert_returns_none_outside_coverage():
    assert fx.convert(5, "VND", "EUR", {"USD": 1.1}) is None
    assert fx.convert(5, "EUR", "VND", {"USD": 1.1}) is None


def test_get_rates_prefers_fresh_cache(monkeypatch, tmp_path):
    cache = tmp_path / "ecb.xml"
    cache.write_bytes(SAMPLE_XML)
    monkeypatch.setattr(fx, "cache_path", lambda: cache)
    monkeypatch.setattr(
        fx, "fetch_rates_xml", lambda **k: (_ for _ in ()).throw(AssertionError("must not fetch"))
    )
    table = fx.get_rates()
    assert table.rates["USD"] == 1.1
    assert table.stale is False


def test_get_rates_serves_stale_cache_when_fetch_fails(monkeypatch, tmp_path):
    cache = tmp_path / "ecb.xml"
    cache.write_bytes(SAMPLE_XML)
    old = time.time() - fx.CACHE_TTL_SECONDS - 60
    os.utime(cache, (old, old))
    monkeypatch.setattr(fx, "cache_path", lambda: cache)

    def boom(**kwargs):
        raise fx.FxError("offline")

    monkeypatch.setattr(fx, "fetch_rates_xml", boom)
    table = fx.get_rates()
    assert table.stale is True
    assert table.as_of == "2026-10-06"


def test_get_rates_refetches_corrupt_fresh_cache(monkeypatch, tmp_path):
    cache = tmp_path / "ecb.xml"
    cache.write_bytes(b"<html>poisoned</html>")
    monkeypatch.setattr(fx, "cache_path", lambda: cache)
    monkeypatch.setattr(fx, "fetch_rates_xml", lambda **k: SAMPLE_XML)
    table = fx.get_rates()
    assert table.rates["USD"] == 1.1
    assert table.stale is False


def test_get_rates_raises_when_nothing_available(monkeypatch, tmp_path):
    monkeypatch.setattr(fx, "cache_path", lambda: tmp_path / "missing.xml")

    def boom(**kwargs):
        raise fx.FxError("offline")

    monkeypatch.setattr(fx, "fetch_rates_xml", boom)
    with pytest.raises(fx.FxError):
        fx.get_rates()
