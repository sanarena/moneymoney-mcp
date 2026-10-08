"""Tests for the pure analytics module (synthetic rows only)."""

from __future__ import annotations

import pytest

from moneymoney_mcp import analytics


def _tx(name, amount, currency="EUR", date="2026-01-15", category="Food"):
    return {
        "name": name,
        "amount": amount,
        "currency": currency,
        "bookingDate": date,
        "category": category,
        "category_path": category,
    }


def test_build_category_paths_nests_by_indentation():
    cats = [
        {"uuid": "a", "name": "Food", "indentation": 0},
        {"uuid": "b", "name": "Groceries", "indentation": 1},
        {"uuid": "c", "name": "Restaurants", "indentation": 1},
        {"uuid": "d", "name": "Travel", "indentation": 0},
    ]
    assert analytics.build_category_paths(cats) == {
        "a": "Food",
        "b": "Food > Groceries",
        "c": "Food > Restaurants",
        "d": "Travel",
    }


def test_build_category_paths_tolerates_bad_levels():
    cats = [
        {"uuid": "a", "name": "X", "indentation": "bogus"},
        {"name": "orphan", "indentation": 5},
    ]
    assert analytics.build_category_paths(cats) == {"a": "X"}


def test_summarize_groups_and_totals_per_currency():
    rows = [
        _tx("FreshMart", -40, "EUR", category="Food"),
        _tx("FreshMart", -10, "EUR", category="Food"),
        _tx("FuelStop", -50, "USD", category="Car"),
        {"name": "broken"},  # no amount: counted, not totalled
    ]
    out = analytics.summarize(rows)
    assert out["count"] == 4
    assert out["totals"] == {"EUR": -50.0, "USD": -50.0}
    assert out["groups"][0]["key"] == "Food"
    assert out["groups"][0]["totals"] == {"EUR": -50.0}


def test_summarize_by_counterparty():
    rows = [_tx("FreshMart", -40), _tx("freshmart  ", -10), _tx("FuelStop", -5)]
    out = analytics.summarize(rows, group_by="counterparty")
    assert [g["key"] for g in out["groups"]] == ["FreshMart", "freshmart  ", "FuelStop"]


def test_compare_merges_disjoint_keys():
    a = analytics.summarize([_tx("FreshMart", -40, category="Food")])
    b = analytics.summarize([_tx("FuelStop", -50, category="Car")])
    out = analytics.compare_summaries(a, b)
    assert out["count_a"] == 1 and out["count_b"] == 1
    assert out["totals_a"] == {"EUR": -40.0}
    assert [g["key"] for g in out["groups"]] == ["Food", "Car"]
    assert out["groups"][0]["count_b"] == 0


def test_top_counterparties_caps_n():
    rows = [_tx(f"shop-{i}", -1) for i in range(5)]
    assert len(analytics.top_counterparties(rows, n=2)) == 2


def _monthly(name, amount, currency="EUR", start="2026-01-05", count=4):
    from datetime import date, timedelta

    day = date.fromisoformat(start)
    rows = []
    for i in range(count):
        rows.append(_tx(name, amount, currency, (day + timedelta(days=30 * i)).isoformat()))
    return rows


def test_detect_recurring_labels_monthly_cadence():
    rows = _monthly("Netflix", -12.99) + [_tx("once", -99)]
    found = analytics.detect_recurring(rows)
    assert len(found) == 1
    sub = found[0]
    assert sub["name"] == "Netflix"
    assert sub["cadence"] == "monthly"
    assert sub["count"] == 4
    assert sub["avg_amount"] == -12.99
    assert sub["first_date"] == "2026-01-05"
    assert sub["next_expected_date"] == "2026-05-05"


def test_detect_recurring_tolerates_amount_drift():
    rows = _monthly("Gym", -50) + _monthly("Gym", -52, start="2026-05-05", count=1)
    found = analytics.detect_recurring(rows, min_occurrences=5)
    assert len(found) == 1  # -52 is within ±5% of -50
    assert found[0]["min_amount"] == -52.0


def test_detect_recurring_splits_distinct_amounts():
    rows = _monthly("Shady", -50) + _monthly("Shady", -500, start="2026-05-05")
    found = analytics.detect_recurring(rows, min_occurrences=3)
    assert sorted(f["avg_amount"] for f in found) == [-500.0, -50.0]


def test_detect_recurring_drops_single_day_and_rare():
    same_day = [_tx("ATM", -100, date="2026-03-01") for _ in range(4)]
    rare = _monthly("Odd", -7, count=2)
    assert analytics.detect_recurring(same_day + rare) == []


def test_detect_recurring_marks_irregular():
    rows = [
        _tx("Free", -20, date="2026-01-01"),
        _tx("Free", -20, date="2026-01-20"),
        _tx("Free", -20, date="2026-06-01"),
    ]
    found = analytics.detect_recurring(rows)
    assert found[0]["cadence"] == "irregular"


def test_category_total_by_name_and_prefix():
    rows = [
        _tx("a", -10, category="Food > Groceries"),
        _tx("b", -20, category="Food > Restaurants"),
        _tx("c", -30, category="Business"),
    ]
    by_name = analytics.category_total(rows, name="Groceries")
    assert (by_name["count"], by_name["total"]) == (1, -10.0)
    by_prefix = analytics.category_total(rows, prefix="Food")
    assert (by_prefix["count"], by_prefix["total"]) == (2, -30.0)
    assert analytics.category_total(rows, prefix="Bus")["count"] == 0
    assert analytics.category_total(rows, prefix="food")["count"] == 2


def test_category_total_multi_currency_has_no_scalar():
    rows = [_tx("a", -10, "EUR"), _tx("b", -10, "USD")]
    out = analytics.category_total(rows, prefix="Food")
    assert out["totals"] == {"EUR": -10.0, "USD": -10.0}
    assert "total" not in out


def test_totals_have_no_float_dust_but_keep_crypto_precision():
    rows = [_tx("a", 0.1, "EUR"), _tx("b", 0.2, "EUR"), _tx("c", 0.00012345, "BTC")]
    out = analytics.summarize(rows, group_by="counterparty")
    assert out["totals"] == {"EUR": 0.3, "BTC": 0.00012345}


def test_cashflow_splits_income_expenses_net_monthly():
    rows = [
        _tx("pay", 2000, "EUR", "2026-01-15"),
        _tx("rent", -800, "EUR", "2026-01-05"),
        _tx("rent", -800, "EUR", "2026-02-05"),
        _tx("gift", 50, "USD", "2026-02-10"),
        {"name": "nodate", "amount": -5},
    ]
    out = analytics.cashflow(rows)
    assert [p["period"] for p in out["periods"]] == ["2026-01", "2026-02"]
    assert out["periods"][0]["income"] == {"EUR": 2000.0}
    assert out["periods"][0]["expenses"] == {"EUR": -800.0}
    assert out["periods"][0]["net"] == {"EUR": 1200.0}
    assert out["periods"][1]["net"] == {"EUR": -800.0, "USD": 50.0}
    assert out["undated_count"] == 1


def test_cashflow_quarterly_and_yearly_keys():
    rows = [_tx("x", -1, date="2026-05-01"), _tx("x", -1, date="2026-11-01")]
    assert [p["period"] for p in analytics.cashflow(rows, "quarterly")["periods"]] == [
        "2026-Q2",
        "2026-Q4",
    ]
    assert analytics.cashflow(rows, "yearly")["periods"][0]["period"] == "2026"
    with pytest.raises(ValueError, match="freq"):
        analytics.cashflow(rows, "weekly")


def test_budget_report_matches_paths_and_skips_empty():
    cats = [
        {"uuid": "a", "name": "Food", "indentation": 0, "currency": "EUR",
         "budget": {"amount": 500, "available": 200, "period": "month"}},
        {"uuid": "b", "name": "Groceries", "indentation": 1, "currency": "EUR",
         "budget": {"amount": 300, "available": 100, "period": "month"}},
        {"uuid": "c", "name": "Fun", "indentation": 0, "budget": {}},
    ]
    rows = [
        _tx("r", -150, category="Food"),
        _tx("r", -100, category="Food > Groceries"),
        _tx("pay", 999, category="Food"),
    ]
    out = analytics.budget_report(cats, rows)
    assert out["unbudgeted_count"] == 1
    assert [b["path"] for b in out["budgets"]] == ["Food", "Food > Groceries"]
    assert out["budgets"][0]["spent"] == {"EUR": -250.0}  # income excluded, subtree included
    assert out["budgets"][0]["transaction_count"] == 2
    assert out["budgets"][0]["budget"]["available"] == 200
    assert out["budgets"][1]["spent"] == {"EUR": -100.0}
    assert out["budgets"][1]["transaction_count"] == 1


def test_category_total_needs_exactly_one_selector():
    with pytest.raises(ValueError):
        analytics.category_total([], name="x", prefix="x")
    with pytest.raises(ValueError):
        analytics.category_total([])
