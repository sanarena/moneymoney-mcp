"""Pure analytics over MoneyMoney transactions and categories.

All functions here are pure (no I/O): they take plain parsed rows and return
plain JSON-serializable values, so they are easy to test and reuse.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

Row = dict[str, Any]

UNKNOWN_CATEGORY = "Uncategorized"
UNKNOWN_COUNTERPARTY = "Unknown"


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _norm_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).lower()


def build_category_paths(categories: list) -> dict[str, str]:
    """Map category UUID to its full path (e.g. ``Food > Groceries``).

    MoneyMoney exports categories as a flat list with ``indentation`` levels;
    this reconstructs the outline with a standard stack algorithm.
    """
    paths: dict[str, str] = {}
    stack: list[str] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        try:
            level = int(cat.get("indentation", 0))
        except (TypeError, ValueError):
            level = 0
        name = cat.get("name") or UNKNOWN_CATEGORY
        stack = stack[: max(level, 0)] + [name]
        if cat.get("uuid"):
            paths[cat["uuid"]] = " > ".join(stack)
    return paths


def _amount_of(row: Row) -> float | None:
    try:
        return float(row.get("amount"))
    except (TypeError, ValueError):
        return None


def _clean(value: float) -> float:
    """Strip float summation dust while keeping crypto precision (8 dp)."""
    return round(value, 8)


def summarize(rows: list[Row], group_by: str = "category") -> dict[str, Any]:
    """Aggregate rows into per-group totals plus grand totals.

    ``group_by`` is ``category`` (resolved path, falls back to the raw name)
    or ``counterparty``. Totals are always per currency; no FX is applied.
    """
    groups: dict[str, dict[str, Any]] = {}
    totals: dict[str, float] = {}
    for row in rows:
        amount = _amount_of(row)
        currency = row.get("currency") or "?"
        if group_by == "counterparty":
            key = row.get("name") or UNKNOWN_COUNTERPARTY
        else:
            key = row.get("category_path") or row.get("category") or UNKNOWN_CATEGORY
        group = groups.setdefault(key, {"key": key, "count": 0, "totals": {}})
        group["count"] += 1
        if amount is not None:
            group["totals"][currency] = group["totals"].get(currency, 0.0) + amount
            totals[currency] = totals.get(currency, 0.0) + amount
    ordered = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    for group in ordered:
        group["totals"] = {cur: _clean(v) for cur, v in group["totals"].items()}
    return {
        "count": len(rows),
        "totals": {cur: _clean(v) for cur, v in totals.items()},
        "groups": ordered,
    }


def compare_summaries(
    summary_a: dict[str, Any], summary_b: dict[str, Any]
) -> dict[str, Any]:
    """Merge two :func:`summarize` outputs into a side-by-side comparison."""
    keys = [g["key"] for g in summary_a["groups"]] + [
        g["key"] for g in summary_b["groups"] if g["key"] not in [x["key"] for x in summary_a["groups"]]
    ]
    by_a = {g["key"]: g for g in summary_a["groups"]}
    by_b = {g["key"]: g for g in summary_b["groups"]}
    groups = [
        {
            "key": key,
            "count_a": by_a.get(key, {}).get("count", 0),
            "count_b": by_b.get(key, {}).get("count", 0),
            "totals_a": by_a.get(key, {}).get("totals", {}),
            "totals_b": by_b.get(key, {}).get("totals", {}),
        }
        for key in keys
    ]
    return {
        "count_a": summary_a["count"],
        "count_b": summary_b["count"],
        "totals_a": summary_a["totals"],
        "totals_b": summary_b["totals"],
        "groups": groups,
    }


def cashflow(rows: list[Row], freq: str = "monthly") -> dict[str, Any]:
    """Income vs expenses vs net per period, ascending by time.

    ``freq`` is ``monthly`` ("2026-09"), ``quarterly`` ("2026-Q3") or
    ``yearly`` ("2026"). Income sums positive amounts, expenses negative
    ones; both stay per currency. Rows without a parseable booking date
    are counted as undated instead of being placed.
    """
    if freq not in ("monthly", "quarterly", "yearly"):
        raise ValueError(f'freq must be monthly, quarterly or yearly, got {freq!r}.')
    periods: dict[str, dict[str, Any]] = {}
    undated = 0
    for row in rows:
        day = _parse_date(row.get("bookingDate"))
        amount = _amount_of(row)
        if day is None or amount is None:
            undated += 1
            continue
        if freq == "monthly":
            key = day.strftime("%Y-%m")
        elif freq == "quarterly":
            key = f"{day.year}-Q{(day.month - 1) // 3 + 1}"
        else:
            key = str(day.year)
        period = periods.setdefault(
            key, {"period": key, "count": 0, "income": {}, "expenses": {}, "net": {}}
        )
        currency = row.get("currency") or "?"
        period["count"] += 1
        bucket = period["income"] if amount >= 0 else period["expenses"]
        bucket[currency] = bucket.get(currency, 0.0) + amount
        period["net"][currency] = period["net"].get(currency, 0.0) + amount
    ordered = [periods[key] for key in sorted(periods)]
    for period in ordered:
        for field in ("income", "expenses", "net"):
            period[field] = {cur: _clean(v) for cur, v in period[field].items()}
    return {"periods": ordered, "undated_count": undated}


def budget_report(
    categories: list, rows: list[Row]
) -> dict[str, Any]:
    """Match per-category budgets against actual spend in ``rows``.

    Only categories with a non-empty ``budget`` dict are reported; the raw
    budget (MoneyMoney's own ``amount``/``available``/``period``) passes
    through untouched, and ``spent`` is computed here over the given rows
    (natural-signed per-currency expense totals, whole subtree included).
    """
    paths = build_category_paths(categories)
    expenses = [row for row in rows if (_amount_of(row) or 0) < 0]
    spend = summarize(expenses, group_by="category")
    budgets = []
    unbudgeted = 0
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        budget = cat.get("budget")
        if not isinstance(budget, dict) or not budget:
            unbudgeted += 1
            continue
        path = paths.get(cat.get("uuid"), cat.get("name") or UNKNOWN_CATEGORY)
        # A budget covers its whole subtree, mirroring MoneyMoney itself.
        spent: dict[str, float] = {}
        count = 0
        for group in spend["groups"]:
            if group["key"] == path or group["key"].startswith(path + " > "):
                count += group["count"]
                for currency, total in group["totals"].items():
                    spent[currency] = spent.get(currency, 0.0) + total
        budgets.append(
            {
                "uuid": cat.get("uuid"),
                "path": path,
                "currency": cat.get("currency"),
                "budget": budget,
                "spent": {cur: _clean(v) for cur, v in spent.items()},
                "transaction_count": count,
            }
        )
    budgets.sort(key=lambda b: b["path"])
    return {"budgets": budgets, "unbudgeted_count": unbudgeted}


def top_counterparties(rows: list[Row], n: int = 10) -> list[dict[str, Any]]:
    """Rank counterparties by transaction count with per-currency totals."""
    summary = summarize(rows, group_by="counterparty")
    return summary["groups"][: max(n, 1)]


def _same_amount(a: float, ref: float) -> bool:
    return abs(a - ref) <= max(0.05 * abs(ref), 1.0)


def _cadence_label(median_days: float) -> str:
    if 6 <= median_days <= 8:
        return "weekly"
    if 13 <= median_days <= 16:
        return "biweekly"
    if 27 <= median_days <= 33:
        return "monthly"
    if 89 <= median_days <= 95:
        return "quarterly"
    if 179 <= median_days <= 187:
        return "semiannual"
    if 360 <= median_days <= 372:
        return "yearly"
    return "irregular"


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def detect_recurring(rows: list[Row], min_occurrences: int = 3) -> list[dict[str, Any]]:
    """Find probable subscriptions / recurring payments.

    Clusters by counterparty, currency and stable amount (within ±5% with an
    absolute floor of 1.0). Clusters with fewer than ``min_occurrences`` rows
    or fewer than 2 distinct dates are dropped. Each surviving cluster gets a
    cadence label from the median interval plus the next expected date.
    """
    dated = [(r, _parse_date(r.get("bookingDate"))) for r in rows]
    dated = [(r, d) for r, d in dated if d is not None and _amount_of(r) is not None]
    dated.sort(key=lambda rd: (_norm_name(rd[0].get("name")), str(rd[0].get("currency")), _amount_of(rd[0])))

    clusters: list[list[tuple[Row, datetime]]] = []
    for row, day in dated:
        amount = _amount_of(row)
        key = (_norm_name(row.get("name")), str(row.get("currency")))
        if (
            clusters
            and (_norm_name(clusters[-1][0][0].get("name")), str(clusters[-1][0][0].get("currency"))) == key
            and _same_amount(amount, _amount_of(clusters[-1][0][0]))
        ):
            clusters[-1].append((row, day))
        else:
            clusters.append([(row, day)])

    found = []
    for cluster in clusters:
        if len(cluster) < max(min_occurrences, 2):
            continue
        days = sorted(d for _, d in cluster)
        if days[0] == days[-1]:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        median_gap = _median([float(g) for g in gaps]) if gaps else 0.0
        amounts = [_amount_of(r) for r, _ in cluster]
        first = cluster[0][0]
        found.append(
            {
                "name": first.get("name") or UNKNOWN_COUNTERPARTY,
                "currency": first.get("currency"),
                "count": len(cluster),
                "avg_amount": _clean(sum(amounts) / len(amounts)),
                "min_amount": min(amounts),
                "max_amount": max(amounts),
                "cadence": _cadence_label(median_gap),
                "median_interval_days": median_gap,
                "first_date": days[0].date().isoformat(),
                "last_date": days[-1].date().isoformat(),
                "next_expected_date": (
                    days[-1] + timedelta(days=round(median_gap))
                ).date().isoformat(),
            }
        )
    found.sort(key=lambda f: f["count"], reverse=True)
    return found


def category_total(
    rows: list[Row], name: str | None = None, prefix: str | None = None
) -> dict[str, Any]:
    """Sum + count rows matching a category name or path prefix.

    Matching is case-insensitive; ``prefix`` matches whole path segments, so
    ``"Bus"`` does not match ``"Business"``.
    """
    if (name is None) == (prefix is None):
        raise ValueError("Pass exactly one of name or prefix.")
    matched = []
    for row in rows:
        path = row.get("category_path") or row.get("category") or UNKNOWN_CATEGORY
        if name is not None:
            leaf = path.split(" > ")[-1]
            if leaf.lower() == name.lower() or path.lower() == name.lower():
                matched.append(row)
        else:
            segments = [s.strip().lower() for s in path.split(">")]
            wanted = [s.strip().lower() for s in prefix.split(">")]
            if segments[: len(wanted)] == wanted:
                matched.append(row)
    totals: dict[str, float] = {}
    for row in matched:
        amount = _amount_of(row)
        if amount is not None:
            currency = row.get("currency") or "?"
            totals[currency] = totals.get(currency, 0.0) + amount
    totals = {cur: _clean(v) for cur, v in totals.items()}
    result: dict[str, Any] = {"count": len(matched), "totals": totals}
    if len(totals) == 1:
        currency, total = next(iter(totals.items()))
        result["total"] = total
        result["currency"] = currency
    return result
