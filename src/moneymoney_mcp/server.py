"""Read-only MCP server exposing MoneyMoney banking data to AI clients.

Stdlib only, no third-party dependencies: the MCP protocol is spoken
directly over stdio (newline-delimited JSON-RPC), so this runs on stock
macOS ``/usr/bin/python3`` (3.9+) with zero installation.

All data comes from the MoneyMoney macOS app through its AppleScript API,
so the app must be running with its database unlocked. No data is ever
modified.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

if __package__ in (None, ""):
    # Executed directly as `python3 path/to/server.py`: make the package
    # importable without installation.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moneymoney_mcp import analytics, bridge, fx, privacy, statements  # noqa: E402

SERVER_NAME = "moneymoney"
SERVER_VERSION = "0.4.0"

DEFAULT_RANGE_DAYS = 90
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _err(message: str) -> str:
    return f"Error: {message}"


def _default_start() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=DEFAULT_RANGE_DAYS)).isoformat()


def _unwrap(rows: Any, key: str) -> list:
    if isinstance(rows, dict):
        return rows.get(key) or []
    if isinstance(rows, list):
        return rows
    return [rows]


def _add_category_names(rows: list) -> None:
    """Resolve each transaction's categoryUuid to name and full path.

    Best effort: if the category list is unavailable, transactions keep
    their categoryUuid and no error is raised.
    """
    uuids = {
        row.get("categoryUuid")
        for row in rows
        if isinstance(row, dict) and row.get("categoryUuid")
    }
    if not uuids:
        return
    try:
        categories = bridge.export_categories()
    except bridge.MoneyMoneyError:
        return
    if not isinstance(categories, list):
        return
    names = {
        cat["uuid"]: cat.get("name")
        for cat in categories
        if isinstance(cat, dict) and cat.get("uuid") in uuids
    }
    paths = analytics.build_category_paths(categories)
    for row in rows:
        if isinstance(row, dict) and row.get("categoryUuid") in names:
            if "category" not in row:
                row["category"] = names[row["categoryUuid"]]
            if row["categoryUuid"] in paths and "category_path" not in row:
                row["category_path"] = paths[row["categoryUuid"]]


def _add_account_names(rows: list) -> None:
    """Resolve each transaction's accountUuid to a readable account name.

    Best effort, like categories: failures leave rows untouched.
    """
    uuids = {
        row.get("accountUuid")
        for row in rows
        if isinstance(row, dict) and row.get("accountUuid")
    }
    if not uuids:
        return
    try:
        accounts = bridge.export_accounts()
    except bridge.MoneyMoneyError:
        return
    if not isinstance(accounts, list):
        return
    names = {
        acc["uuid"]: acc.get("name")
        for acc in accounts
        if isinstance(acc, dict) and acc.get("uuid") in uuids
    }
    for row in rows:
        if (
            isinstance(row, dict)
            and row.get("accountUuid") in names
            and "account" not in row
        ):
            row["account"] = names[row["accountUuid"]]


def _fetch_rows(
    start: str,
    to_date: str | None,
    account: str | None,
    category: str | None,
) -> list:
    data = bridge.export_transactions(
        from_date=start, to_date=to_date, account=account, category=category
    )
    rows = _unwrap(data, "transactions")
    _add_category_names(rows)
    _add_account_names(rows)
    return rows


def _first_of_month() -> str:
    from datetime import date

    return date.today().replace(day=1).isoformat()


def _convert_totals(
    totals: dict[str, float], convert_to: str
) -> tuple[float, list[str], dict[str, Any]]:
    """Convert per-currency totals; report what could not be converted."""
    table = fx.get_rates()
    target = convert_to.upper()
    converted = 0.0
    unconverted: list[str] = []
    for currency, total in totals.items():
        part = fx.convert(total, currency, target, table.rates)
        if part is None:
            unconverted.append(currency)
        else:
            converted += part
    info: dict[str, Any] = {"rates_as_of": table.as_of, "rates_stale": table.stale}
    return converted, sorted(unconverted), info


def get_status() -> str:
    """Check whether MoneyMoney is running and its database unlocked."""
    try:
        accounts = bridge.export_accounts(timeout=15)
    except bridge.DatabaseLockedError:
        return _ok({"moneymoney_running": True, "database_unlocked": False})
    except bridge.MoneyMoneyError as exc:
        if "did not answer" in str(exc):
            return _ok(
                {
                    "moneymoney_running": True,
                    "database_unlocked": None,
                    "note": "MoneyMoney did not answer in time; it may be busy.",
                }
            )
        return _ok(
            {"moneymoney_running": False, "database_unlocked": None, "detail": str(exc)}
        )
    count = len(accounts) if isinstance(accounts, list) else None
    return _ok(
        {
            "moneymoney_running": True,
            "database_unlocked": True,
            "account_count": count,
        }
    )


def list_accounts(mask_ids: bool | None = None) -> str:
    """List all MoneyMoney accounts with their current balances."""
    try:
        accounts = bridge.export_accounts()
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    if privacy.masking_enabled(mask_ids) and isinstance(accounts, list):
        accounts = [
            privacy.mask_account(a) if isinstance(a, dict) else a for a in accounts
        ]
    return _ok({"accounts": accounts})


def list_categories() -> str:
    """List all MoneyMoney transaction categories."""
    try:
        categories = bridge.export_categories()
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    return _ok({"categories": categories})


def get_transactions(
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    category: str | None = None,
    limit: int = DEFAULT_LIMIT,
    mask_ids: bool | None = None,
) -> str:
    """Get MoneyMoney transactions, newest first."""
    if not 1 <= limit <= MAX_LIMIT:
        return _err(f"limit must be between 1 and {MAX_LIMIT}, got {limit}.")
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, category)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    if privacy.masking_enabled(mask_ids):
        rows = [privacy.mask_transaction(r) if isinstance(r, dict) else r for r in rows]
    total = len(rows)
    return _ok(
        {
            "from_date": start,
            "to_date": to_date,
            "account": account,
            "category": category,
            "total_count": total,
            "truncated": total > limit,
            "transactions": rows[:limit],
        }
    )


def search_transactions(
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    category: str | None = None,
    counterparty: str | None = None,
    purpose: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = DEFAULT_LIMIT,
    mask_ids: bool | None = None,
) -> str:
    """Search transactions by text and amount, newest first."""
    if not 1 <= limit <= MAX_LIMIT:
        return _err(f"limit must be between 1 and {MAX_LIMIT}, got {limit}.")
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, category)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    matched = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if counterparty and counterparty.lower() not in str(row.get("name", "")).lower():
            continue
        if purpose and purpose.lower() not in str(row.get("purpose", "")).lower():
            continue
        if min_amount is not None or max_amount is not None:
            try:
                amount = float(row.get("amount"))
            except (TypeError, ValueError):
                continue
            if min_amount is not None and amount < min_amount:
                continue
            if max_amount is not None and amount > max_amount:
                continue
        matched.append(row)
    if privacy.masking_enabled(mask_ids):
        matched = [privacy.mask_transaction(r) for r in matched]
    return _ok(
        {
            "from_date": start,
            "to_date": to_date,
            "account": account,
            "category": category,
            "counterparty": counterparty,
            "purpose": purpose,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "matched_count": len(matched),
            "truncated": len(matched) > limit,
            "transactions": matched[:limit],
        }
    )


def get_portfolio(account: str | None = None) -> str:
    """Get securities holdings (depot positions) with current values."""
    try:
        data = bridge.export_portfolio(account=account)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    return _ok({"account": account, "positions": _unwrap(data, "portfolio")})


def top_counterparties(
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    n: int = 10,
    convert_to: str | None = None,
) -> str:
    """Rank counterparties by activity with per-currency totals."""
    if not 1 <= n <= 100:
        return _err(f"n must be between 1 and 100, got {n}.")
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, None)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    groups = analytics.top_counterparties(rows, n=n)
    payload: dict[str, Any] = {
        "from_date": start,
        "to_date": to_date,
        "account": account,
        "n": n,
        "convert_to": convert_to.upper() if convert_to else None,
        "counterparties": [
            {"name": g["key"], "count": g["count"], "totals": g["totals"]}
            for g in groups
        ],
    }
    if convert_to:
        try:
            table = fx.get_rates()
        except fx.FxError as exc:
            payload["fx_error"] = str(exc)
            return _ok(payload)
        target = convert_to.upper()
        unconverted: set[str] = set()
        for entry in payload["counterparties"]:
            total = 0.0
            for currency, subtotal in entry["totals"].items():
                part = fx.convert(subtotal, currency, target, table.rates)
                if part is None:
                    unconverted.add(currency)
                else:
                    total += part
            entry["converted_total"] = round(total, 2)
        payload["counterparties"].sort(
            key=lambda e: abs(e["converted_total"]), reverse=True
        )
        payload["rates_as_of"] = table.as_of
        payload["rates_stale"] = table.stale
        payload["unconverted_currencies"] = sorted(unconverted)
    return _ok(payload)


def get_recurring(
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    min_occurrences: int = 3,
) -> str:
    """Detect subscriptions and recurring payments with cadence labels."""
    if not 2 <= min_occurrences <= 100:
        return _err(f"min_occurrences must be between 2 and 100, got {min_occurrences}.")
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, None)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    return _ok(
        {
            "from_date": start,
            "to_date": to_date,
            "account": account,
            "min_occurrences": min_occurrences,
            "subscriptions": analytics.detect_recurring(rows, min_occurrences),
        }
    )


def compare_periods(
    from_a: str,
    to_a: str,
    from_b: str,
    to_b: str,
    account: str | None = None,
    group_by: str = "category",
) -> str:
    """Compare two date ranges side by side (counts and per-currency totals)."""
    if group_by not in ("category", "counterparty"):
        return _err(f'group_by must be "category" or "counterparty", got {group_by!r}.')
    try:
        rows_a = _fetch_rows(from_a, to_a, account, None)
        rows_b = _fetch_rows(from_b, to_b, account, None)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    comparison = analytics.compare_summaries(
        analytics.summarize(rows_a, group_by), analytics.summarize(rows_b, group_by)
    )
    return _ok(
        {
            "period_a": {"from_date": from_a, "to_date": to_a},
            "period_b": {"from_date": from_b, "to_date": to_b},
            "account": account,
            "group_by": group_by,
            **comparison,
        }
    )


def get_category_total(
    category: str | None = None,
    prefix: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    convert_to: str | None = None,
) -> str:
    """Sum + count for one category or a whole category subtree."""
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, None)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    try:
        result = analytics.category_total(rows, name=category, prefix=prefix)
    except ValueError as exc:
        return _err(str(exc))
    payload: dict[str, Any] = {
        "category": category,
        "prefix": prefix,
        "from_date": start,
        "to_date": to_date,
        "account": account,
        **result,
    }
    if convert_to:
        try:
            converted, unconverted, info = _convert_totals(result["totals"], convert_to)
        except fx.FxError as exc:
            payload["fx_error"] = str(exc)
            return _ok(payload)
        payload["convert_to"] = convert_to.upper()
        payload["converted_total"] = round(converted, 2)
        payload["unconverted_currencies"] = unconverted
        payload.update(info)
    return _ok(payload)


def list_statements(
    account: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> str:
    """List electronic bank statements MoneyMoney downloaded, newest first."""
    if not 1 <= limit <= 1000:
        return _err(f"limit must be between 1 and 1000, got {limit}.")
    try:
        found = statements.list_statements(account=account, since=since)
    except statements.StatementsError as exc:
        return _err(str(exc))
    slim = [
        {k: entry[k] for k in ("account", "name", "size", "modified") if k in entry}
        for entry in found
    ]
    return _ok(
        {
            "account": account,
            "since": since,
            "total_count": len(slim),
            "truncated": len(slim) > limit,
            "statements": slim[:limit],
        }
    )


def get_statement(
    account: str,
    name: str,
    max_chars: int = statements.DEFAULT_MAX_CHARS,
    mask_ids: bool | None = None,
) -> str:
    """Read one electronic bank statement with extracted text.

    Text extraction is best effort: `extraction` reports "full",
    "partial" (some text unmapped) or "unavailable" (no readable text).
    """
    try:
        entry = statements.get_statement(account, name, max_chars=max_chars)
    except statements.StatementsError as exc:
        return _err(str(exc))
    entry.pop("path", None)
    if privacy.masking_enabled(mask_ids) and entry.get("text"):
        entry["text"] = privacy.mask_text(entry["text"])
    return _ok(entry)


def cashflow_timeline(
    from_date: str | None = None,
    to_date: str | None = None,
    account: str | None = None,
    freq: str = "monthly",
    convert_to: str | None = None,
) -> str:
    """Income vs expenses vs net per period, oldest first."""
    if freq not in ("monthly", "quarterly", "yearly"):
        return _err(f'freq must be monthly, quarterly or yearly, got {freq!r}.')
    start = from_date or _default_start()
    try:
        rows = _fetch_rows(start, to_date, account, None)
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    try:
        result = analytics.cashflow(rows, freq)
    except ValueError as exc:
        return _err(str(exc))
    payload: dict[str, Any] = {
        "from_date": start,
        "to_date": to_date,
        "account": account,
        "freq": freq,
        **result,
    }
    if convert_to:
        try:
            table = fx.get_rates()
        except fx.FxError as exc:
            payload["fx_error"] = str(exc)
            return _ok(payload)
        target = convert_to.upper()
        unconverted: set[str] = set()
        for period in payload["periods"]:
            for field in ("income", "expenses", "net"):
                total = 0.0
                for currency, subtotal in period[field].items():
                    part = fx.convert(subtotal, currency, target, table.rates)
                    if part is None:
                        unconverted.add(currency)
                    else:
                        total += part
                period[f"converted_{field}"] = round(total, 2)
        payload["convert_to"] = target
        payload["rates_as_of"] = table.as_of
        payload["rates_stale"] = table.stale
        payload["unconverted_currencies"] = sorted(unconverted)
    return _ok(payload)


def get_budgets(
    from_date: str | None = None,
    to_date: str | None = None,
    convert_to: str | None = None,
) -> str:
    """Compare per-category budgets against actual spend.

    Defaults to the current calendar month to align with monthly budgets.
    Each entry shows MoneyMoney's own budget (amount/available/period)
    alongside spend computed here over the queried range.
    """
    start = from_date or _first_of_month()
    try:
        rows = _fetch_rows(start, to_date, None, None)
        categories = bridge.export_categories()
    except bridge.MoneyMoneyError as exc:
        return _err(str(exc))
    if not isinstance(categories, list):
        return _err("MoneyMoney returned an unexpected category list.")
    result = analytics.budget_report(categories, rows)
    payload: dict[str, Any] = {
        "from_date": start,
        "to_date": to_date,
        **result,
    }
    if convert_to:
        try:
            table = fx.get_rates()
        except fx.FxError as exc:
            payload["fx_error"] = str(exc)
            return _ok(payload)
        target = convert_to.upper()
        unconverted: set[str] = set()
        for entry in payload["budgets"]:
            total = 0.0
            for currency, subtotal in entry["spent"].items():
                part = fx.convert(subtotal, currency, target, table.rates)
                if part is None:
                    unconverted.add(currency)
                else:
                    total += part
            entry["converted_spent"] = round(total, 2)
        payload["convert_to"] = target
        payload["rates_as_of"] = table.as_of
        payload["rates_stale"] = table.stale
        payload["unconverted_currencies"] = sorted(unconverted)
    return _ok(payload)


# ---------------------------------------------------------------------------
# Tool registry with explicit JSON schemas (replaces SDK codegen).
# ---------------------------------------------------------------------------

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD."}
_ACCOUNT = {
    "type": "string",
    "description": "Account UUID, IBAN, number, name or group name.",
}
_CATEGORY = {
    "type": "string",
    "description": 'Category UUID or name ("A\\B" for nested).',
}
_MASK = {
    "type": "boolean",
    "description": (
        "Mask IBANs/account numbers. Omit for the server default "
        "(MONEYMONEY_MASK_IDS env, off unless set); pass false to reveal "
        "full numbers even when masking is on by default."
    ),
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]
    func: Callable[..., str]


TOOLS: list[ToolDef] = [
    ToolDef(
        "get_status",
        "Check whether MoneyMoney is running and its database unlocked. "
        "Call this first when other tools report errors.",
        _schema({}),
        get_status,
    ),
    ToolDef(
        "list_accounts",
        "List all MoneyMoney accounts with their current balances. Use this "
        "first to discover account names, IBANs or numbers for other tools.",
        _schema({"mask_ids": _MASK}),
        list_accounts,
    ),
    ToolDef(
        "list_categories",
        "List all MoneyMoney transaction categories.",
        _schema({}),
        list_categories,
    ),
    ToolDef(
        "get_transactions",
        "Get MoneyMoney transactions, newest first. Category UUIDs are "
        "resolved to names and full paths.",
        _schema(
            {
                "from_date": {**_DATE, "description": "Start date (default: 90 days ago)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "category": _CATEGORY,
                "limit": {
                    "type": "integer",
                    "description": "Max transactions (1-2000, default 200).",
                    "default": DEFAULT_LIMIT,
                },
                "mask_ids": _MASK,
            }
        ),
        get_transactions,
    ),
    ToolDef(
        "search_transactions",
        "Search transactions by counterparty/purpose substring and amount "
        "range, newest first.",
        _schema(
            {
                "from_date": {**_DATE, "description": "Start date (default: 90 days ago)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "category": _CATEGORY,
                "counterparty": {
                    "type": "string",
                    "description": "Case-insensitive substring of the counterparty name.",
                },
                "purpose": {
                    "type": "string",
                    "description": "Case-insensitive substring of the purpose text.",
                },
                "min_amount": {"type": "number", "description": "Lower amount bound."},
                "max_amount": {"type": "number", "description": "Upper amount bound."},
                "limit": {
                    "type": "integer",
                    "description": "Max transactions (1-2000, default 200).",
                    "default": DEFAULT_LIMIT,
                },
                "mask_ids": _MASK,
            }
        ),
        search_transactions,
    ),
    ToolDef(
        "get_portfolio",
        "Get securities holdings (depot positions) with current values.",
        _schema({"account": _ACCOUNT}),
        get_portfolio,
    ),
    ToolDef(
        "top_counterparties",
        "Rank counterparties by activity with per-currency totals. With "
        "convert_to, totals convert at ECB rates and ranking uses them.",
        _schema(
            {
                "from_date": {**_DATE, "description": "Start date (default: 90 days ago)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "n": {
                    "type": "integer",
                    "description": "How many to return (1-100, default 10).",
                    "default": 10,
                },
                "convert_to": {
                    "type": "string",
                    "description": 'ISO code for converted ranking, e.g. "EUR".',
                },
            }
        ),
        top_counterparties,
    ),
    ToolDef(
        "get_recurring",
        "Detect subscriptions and recurring payments with cadence labels "
        "(weekly/biweekly/monthly/quarterly/semiannual/yearly/irregular).",
        _schema(
            {
                "from_date": {
                    **_DATE,
                    "description": "Start date (default: 90 days ago; use a year for yearly items).",
                },
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "min_occurrences": {
                    "type": "integer",
                    "description": "Minimum charges per cluster (2-100, default 3).",
                    "default": 3,
                },
            }
        ),
        get_recurring,
    ),
    ToolDef(
        "compare_periods",
        "Compare two date ranges side by side (counts and per-currency totals).",
        _schema(
            {
                "from_a": {**_DATE, "description": "Period A start."},
                "to_a": {**_DATE, "description": "Period A end."},
                "from_b": {**_DATE, "description": "Period B start."},
                "to_b": {**_DATE, "description": "Period B end."},
                "account": _ACCOUNT,
                "group_by": {
                    "type": "string",
                    "enum": ["category", "counterparty"],
                    "default": "category",
                },
            },
            required=["from_a", "to_a", "from_b", "to_b"],
        ),
        compare_periods,
    ),
    ToolDef(
        "get_category_total",
        "Sum + count for one category or a whole subtree. Pass exactly one "
        "of category (leaf name or full path) or prefix (path prefix).",
        _schema(
            {
                "category": {"type": "string", "description": "Leaf name or full path."},
                "prefix": {
                    "type": "string",
                    "description": 'Subtree prefix, e.g. "Food".',
                },
                "from_date": {**_DATE, "description": "Start date (default: 90 days ago)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "convert_to": {"type": "string", "description": "ISO code for one-currency total."},
            }
        ),
        get_category_total,
    ),
    ToolDef(
        "list_statements",
        "List electronic bank statements MoneyMoney downloaded, newest first.",
        _schema(
            {
                "account": {
                    "type": "string",
                    "description": "Only statements from this account folder.",
                },
                "since": {
                    "type": "string",
                    "description": "Only statements modified on/after YYYY-MM-DD.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries (1-1000, default 100).",
                    "default": 100,
                },
            }
        ),
        list_statements,
    ),
    ToolDef(
        "cashflow_timeline",
        "Income vs expenses vs net per period, oldest first. Answers "
        "whether saving is going up or down without dumping transactions.",
        _schema(
            {
                "from_date": {**_DATE, "description": "Start date (default: 90 days ago)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "account": _ACCOUNT,
                "freq": {
                    "type": "string",
                    "enum": ["monthly", "quarterly", "yearly"],
                    "default": "monthly",
                },
                "convert_to": {"type": "string", "description": "ISO code for converted totals."},
            }
        ),
        cashflow_timeline,
    ),
    ToolDef(
        "get_budgets",
        "Compare per-category budgets against actual spend. Defaults to "
        "the current month to align with monthly budgets.",
        _schema(
            {
                "from_date": {**_DATE, "description": "Start date (default: first of this month)."},
                "to_date": {**_DATE, "description": "End date (default: open end)."},
                "convert_to": {"type": "string", "description": "ISO code for converted spent."},
            }
        ),
        get_budgets,
    ),
    ToolDef(
        "get_statement",
        "Read one electronic bank statement with extracted text. The "
        "`extraction` field reports full/partial/unavailable quality.",
        _schema(
            {
                "account": {
                    "type": "string",
                    "description": "Account folder from list_statements.",
                },
                "name": {"type": "string", "description": "File name from list_statements."},
                "max_chars": {
                    "type": "integer",
                    "description": "Max text characters (default 8000).",
                    "default": statements.DEFAULT_MAX_CHARS,
                },
                "mask_ids": _MASK,
            },
            required=["account", "name"],
        ),
        get_statement,
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


# ---------------------------------------------------------------------------
# Minimal MCP-over-stdio transport (newline-delimited JSON-RPC).
# ---------------------------------------------------------------------------


def _error(code: int, message: str, msg_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_message(msg: Any) -> dict[str, Any] | None:
    """Handle one parsed JSON-RPC message; None means "no response"."""
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    msg_id = msg.get("id")
    if not isinstance(method, str):
        return _error(-32600, "Invalid Request: missing method.", msg_id) if "id" in msg else None
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(-32600, "Invalid Request: params must be an object.", msg_id) if "id" in msg else None

    if method == "initialize":
        version = params.get("protocolVersion", "2025-06-18")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.schema,
                    }
                    for tool in TOOLS
                ]
            },
        }
    if method == "tools/call":
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _error(-32602, f"Unknown tool: {name!r}.", msg_id)
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(-32602, "Tool arguments must be an object.", msg_id)
        try:
            text = tool.func(**arguments)
        except TypeError as exc:
            return _error(-32602, f"Invalid tool arguments: {exc}", msg_id)
        except Exception as exc:  # noqa: BLE001 - protocol must not crash
            return _error(-32603, f"Tool failed: {exc}", msg_id)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    if "id" in msg:
        return _error(-32601, f"Method not found: {method}.", msg_id)
    return None


def main() -> None:
    """Serve MCP over stdio. stdout carries frames only; logs go to stderr."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = handle_message(msg)
        except Exception as exc:  # noqa: BLE001 - keep serving
            print(f"moneymoney-mcp: dropped message ({exc})", file=sys.stderr)
            continue
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
