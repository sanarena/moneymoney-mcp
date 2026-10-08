"""Tests for the AppleScript bridge (all subprocess calls mocked)."""

from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from moneymoney_mcp import bridge


SAMPLE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
\t<dict>
\t\t<key>name</key>
\t\t<string>Girokonto</string>
\t\t<key>bookingDate</key>
\t\t<date>2026-10-01T00:00:00Z</date>
\t\t<key>amount</key>
\t\t<real>12.5</real>
\t\t<key>booked</key>
\t\t<true/>
\t</dict>
</array>
</plist>
"""


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_quote_escapes_backslash_and_quotes():
    assert bridge._quote('a"b\\c') == 'a\\"b\\\\c'


def test_validate_date_accepts_iso():
    assert bridge._validate_date("2026-10-07", "from_date") == "2026-10-07"


def test_validate_date_rejects_other_formats():
    with pytest.raises(bridge.MoneyMoneyError, match="YYYY-MM-DD"):
        bridge._validate_date("07.10.2026", "from_date")
    with pytest.raises(bridge.MoneyMoneyError, match="YYYY-MM-DD"):
        bridge._validate_date(20261007, "from_date")


def test_run_applescript_success(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _: "/usr/bin/osascript")
    seen = {}
    kwargs = {}

    def fake_run_kwargs(cmd, **kw):
        seen["cmd"] = cmd
        kwargs.update(kw)
        return _completed(stdout="ok")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run_kwargs)
    assert bridge.run_applescript("export accounts") == "ok"
    assert seen["cmd"][:2] == ["osascript", "-e"]
    assert 'tell application "MoneyMoney"' in seen["cmd"][2]
    assert kwargs["encoding"] == "utf-8"


def test_run_applescript_requires_osascript(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _: None)
    with pytest.raises(bridge.MoneyMoneyError, match="macOS"):
        bridge.run_applescript("export accounts")


def test_run_applescript_locked_database(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **k: _completed(
            stderr="execution error: MoneyMoney got an error: Locked database. (-2720)",
            returncode=1,
        ),
    )
    with pytest.raises(bridge.DatabaseLockedError, match="locked"):
        bridge.run_applescript("export accounts")


def test_run_applescript_generic_2720_is_not_locked(monkeypatch):
    # -2720 is shared by many MoneyMoney errors; only the message locks.
    monkeypatch.setattr(bridge.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **k: _completed(
            stderr="execution error: MoneyMoney got an error: Invalid file format X. (-2720)",
            returncode=1,
        ),
    )
    with pytest.raises(bridge.MoneyMoneyError) as info:
        bridge.run_applescript("export accounts")
    assert type(info.value) is bridge.MoneyMoneyError


def test_run_applescript_other_error(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda *a, **k: _completed(stderr="boom", returncode=1),
    )
    with pytest.raises(bridge.MoneyMoneyError, match="boom"):
        bridge.run_applescript("export accounts")


def test_run_applescript_timeout(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda _: "/usr/bin/osascript")

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=1)

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    with pytest.raises(bridge.MoneyMoneyError, match="did not answer"):
        bridge.run_applescript("export accounts", timeout=1)


def test_export_plist_parses_and_converts_dates(monkeypatch):
    monkeypatch.setattr(bridge, "run_applescript", lambda *a, **k: SAMPLE_PLIST)
    rows = bridge._export_plist("export transactions")
    assert rows == [
        {"name": "Girokonto", "bookingDate": "2026-10-01", "amount": 12.5, "booked": True}
    ]


def test_export_plist_rejects_garbage(monkeypatch):
    monkeypatch.setattr(bridge, "run_applescript", lambda *a, **k: "not xml")
    with pytest.raises(bridge.MoneyMoneyError, match="Could not parse"):
        bridge._export_plist("export accounts")


def test_to_jsonable_drops_icon_blobs():
    assert bridge._to_jsonable({"name": "Giro", "icon": b"\x89PNG\r\n"}) == {
        "name": "Giro"
    }


def test_to_jsonable_keeps_time_when_present():
    assert (
        bridge._to_jsonable(datetime(2026, 10, 7, 14, 30))
        == "2026-10-07T14:30:00"
    )
    assert bridge._to_jsonable(datetime(2026, 10, 7, 12, 0)) == "2026-10-07"
    assert bridge._to_jsonable(b"\xff\xfe") == "��".replace("��", "\ufffd\ufffd")


def test_export_transactions_builds_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        bridge, "_export_plist", lambda cmd, **k: seen.setdefault("cmd", cmd)
    )
    bridge.export_transactions(
        from_date="2026-01-01",
        to_date="2026-03-31",
        account='My "Bank"',
        category="Food\\Groceries",
    )
    assert seen["cmd"] == (
        'export transactions from account "My \\"Bank\\"" '
        'from category "Food\\\\Groceries" '
        'from date "2026-01-01" to date "2026-03-31" as "plist"'
    )


def test_export_transactions_minimal_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        bridge, "_export_plist", lambda cmd, **k: seen.setdefault("cmd", cmd)
    )
    bridge.export_transactions(from_date="2026-01-01")
    assert seen["cmd"] == 'export transactions from date "2026-01-01" as "plist"'


def test_export_transactions_validates_dates(monkeypatch):
    monkeypatch.setattr(bridge, "_export_plist", lambda *a, **k: [])
    with pytest.raises(bridge.MoneyMoneyError, match="from_date"):
        bridge.export_transactions(from_date="01.01.2026")
    with pytest.raises(bridge.MoneyMoneyError, match="to_date"):
        bridge.export_transactions(from_date="2026-01-01", to_date="tomorrow")


def test_export_portfolio_command(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge, "_export_plist", lambda cmd, **k: seen.append(cmd))
    bridge.export_portfolio()
    bridge.export_portfolio(account="Depot")
    assert seen == [
        'export portfolio as "plist"',
        'export portfolio from account "Depot" as "plist"',
    ]
