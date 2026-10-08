"""AppleScript bridge to the MoneyMoney macOS app (read-only).

MoneyMoney's SQLite database is encrypted, so the only supported way to read
data from outside the app is its AppleScript API, which returns XML property
lists. This module shells out to ``osascript`` and converts the results to
plain JSON-serializable Python values.

Requires macOS with MoneyMoney installed, running, and its database unlocked.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from datetime import date, datetime, time
from typing import Any

APP_NAME = "MoneyMoney"
DEFAULT_TIMEOUT = 120


class MoneyMoneyError(Exception):
    """Any failure while talking to MoneyMoney."""


class DatabaseLockedError(MoneyMoneyError):
    """MoneyMoney's database is locked and must be unlocked in the app."""

    def __init__(self) -> None:
        super().__init__(
            "MoneyMoney database is locked. Unlock it in the MoneyMoney app and try again."
        )


def _quote(value: str) -> str:
    """Escape a string for use inside an AppleScript double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validate_date(value: str, name: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise MoneyMoneyError(f"{name} must be YYYY-MM-DD, got {value!r}.") from None
    return value


def run_applescript(command: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run one AppleScript command against MoneyMoney, return stdout text."""
    if shutil.which("osascript") is None:
        raise MoneyMoneyError("osascript not found. moneymoney-mcp requires macOS.")
    script = f'tell application "{APP_NAME}" to {command}'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise MoneyMoneyError(
            f"MoneyMoney did not answer within {timeout:g}s. Is the app running?"
        ) from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        # NOTE: -2720 is generic (locked DB, invalid format/account, ...),
        # so only the message text identifies a locked database.
        if "Locked database" in err:
            raise DatabaseLockedError()
        raise MoneyMoneyError(f"MoneyMoney AppleScript error: {err}")
    return proc.stdout


def _to_jsonable(value: Any) -> Any:
    """Convert plist values (datetime, ...) to JSON-serializable values."""
    if isinstance(value, datetime):
        # MoneyMoney stamps date-only fields at exactly 12:00 to defeat
        # timezone day-shifting; only a different time is a real timestamp.
        if value.time() in (time.min, time(12, 0)):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        # Drop embedded PNG icons: binary bloat, useless to an LLM.
        return {str(k): _to_jsonable(v) for k, v in value.items() if k != "icon"}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _export_plist(command: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    output = run_applescript(command, timeout=timeout)
    try:
        parsed = plistlib.loads(output.encode("utf-8"))
    except Exception as exc:
        raise MoneyMoneyError(
            f"Could not parse MoneyMoney response: {exc}"
        ) from exc
    return _to_jsonable(parsed)


def export_accounts(timeout: float = DEFAULT_TIMEOUT) -> Any:
    """List all accounts with balances. Returns a list of account dicts."""
    return _export_plist("export accounts", timeout=timeout)


def export_categories(timeout: float = DEFAULT_TIMEOUT) -> Any:
    """List all categories. Returns a list of category dicts."""
    return _export_plist("export categories", timeout=timeout)


def export_transactions(
    from_date: str,
    to_date: str | None = None,
    account: str | None = None,
    category: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Export transactions, optionally filtered. Returns {"creator", "transactions"}.

    ``account`` accepts a UUID, IBAN, account number, account name or group
    name. ``category`` accepts a UUID or name; nested names use backslashes
    (e.g. ``Food\\Groceries``). Dates are YYYY-MM-DD.
    """
    _validate_date(from_date, "from_date")
    parts = ["export transactions"]
    if account:
        parts.append(f'from account "{_quote(account)}"')
    if category:
        parts.append(f'from category "{_quote(category)}"')
    parts.append(f'from date "{from_date}"')
    if to_date:
        _validate_date(to_date, "to_date")
        parts.append(f'to date "{to_date}"')
    parts.append('as "plist"')
    return _export_plist(" ".join(parts), timeout=timeout)


def export_portfolio(
    account: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Export securities holdings, optionally for one account. Returns {"creator", "portfolio"}."""
    parts = ["export portfolio"]
    if account:
        parts.append(f'from account "{_quote(account)}"')
    parts.append('as "plist"')
    return _export_plist(" ".join(parts), timeout=timeout)
