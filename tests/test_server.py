"""Tests for the MCP tools (bridge calls mocked)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from moneymoney_mcp import bridge, fx, privacy, server, statements


def _parse(text):
    assert not text.startswith("Error:"), text
    return json.loads(text)


def test_list_accounts_envelope(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_accounts", lambda **k: [{"name": "Giro", "balance": 10.0}]
    )
    assert _parse(server.list_accounts()) == {
        "accounts": [{"name": "Giro", "balance": 10.0}]
    }


def test_list_categories_envelope(monkeypatch):
    monkeypatch.setattr(bridge, "export_categories", lambda **k: [{"name": "Food"}])
    assert _parse(server.list_categories()) == {"categories": [{"name": "Food"}]}


def test_get_portfolio_passes_account(monkeypatch):
    seen = {}

    def fake_export(account=None, **kwargs):
        seen["a"] = account
        return []

    monkeypatch.setattr(bridge, "export_portfolio", fake_export)
    body = _parse(server.get_portfolio(account="Depot"))
    assert seen["a"] == "Depot"
    assert body == {"account": "Depot", "positions": []}


def test_get_transactions_defaults_to_last_90_days(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        bridge,
        "export_transactions",
        lambda **k: seen.update(k) or [],
    )
    body = _parse(server.get_transactions())
    expected = (date.today() - timedelta(days=90)).isoformat()
    assert seen["from_date"] == expected
    assert body["from_date"] == expected
    assert body["total_count"] == 0
    assert body["truncated"] is False


def test_get_transactions_truncates_with_counts(monkeypatch):
    rows = [{"id": i} for i in range(5)]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: rows)
    body = _parse(server.get_transactions(from_date="2026-01-01", limit=2))
    assert body["total_count"] == 5
    assert body["truncated"] is True
    assert body["transactions"] == [{"id": 0}, {"id": 1}]


def test_get_transactions_unwraps_plist_dict_and_joins_categories(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "export_transactions",
        lambda **k: {
            "creator": "MoneyMoney",
            "transactions": [
                {"id": 1, "categoryUuid": "uuid-food"},
                {"id": 2, "categoryUuid": "uuid-unknown"},
                {"id": 3},
            ],
        },
    )
    monkeypatch.setattr(
        bridge, "export_categories", lambda **k: [{"uuid": "uuid-food", "name": "Food"}]
    )
    body = _parse(server.get_transactions(from_date="2026-01-01"))
    assert body["total_count"] == 3
    assert body["transactions"][0]["category"] == "Food"
    assert "category" not in body["transactions"][1]
    assert "category" not in body["transactions"][2]


def test_get_transactions_keeps_uuids_when_categories_fail(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "export_transactions",
        lambda **k: [{"id": 1, "categoryUuid": "uuid-food"}],
    )

    def boom(**kwargs):
        raise bridge.MoneyMoneyError("nope")

    monkeypatch.setattr(bridge, "export_categories", boom)
    body = _parse(server.get_transactions(from_date="2026-01-01"))
    assert body["transactions"] == [{"id": 1, "categoryUuid": "uuid-food"}]


def test_get_portfolio_unwraps_plist_dict(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "export_portfolio",
        lambda account=None, **k: {"creator": "MoneyMoney", "portfolio": [{"name": "AAPL"}]},
    )
    assert _parse(server.get_portfolio()) == {
        "account": None,
        "positions": [{"name": "AAPL"}],
    }


def test_get_transactions_rejects_bad_limit():
    assert server.get_transactions(limit=0).startswith("Error:")
    assert server.get_transactions(limit=2001).startswith("Error:")


def test_tools_surface_bridge_errors_as_text(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "export_accounts",
        lambda **k: (_ for _ in ()).throw(bridge.DatabaseLockedError()),
    )
    assert server.list_accounts().startswith("Error: MoneyMoney database is locked")


def test_server_registers_fourteen_tools():
    assert sorted(server.TOOLS_BY_NAME) == [
        "cashflow_timeline",
        "compare_periods",
        "get_budgets",
        "get_category_total",
        "get_portfolio",
        "get_recurring",
        "get_statement",
        "get_status",
        "get_transactions",
        "list_accounts",
        "list_categories",
        "list_statements",
        "search_transactions",
        "top_counterparties",
    ]
    for tool in server.TOOLS:
        assert tool.schema["type"] == "object"
        assert tool.description


def test_dispatch_initialize_and_list():
    init = server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}}
    )
    assert init["result"]["serverInfo"]["name"] == "moneymoney"
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    listing = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listing["result"]["tools"]) == 14
    assert listing["result"]["tools"][0]["inputSchema"]["type"] == "object"


def test_dispatch_call_ok_and_errors(monkeypatch):
    monkeypatch.setattr(bridge, "export_accounts", lambda **k: [{"name": "Giro"}])
    ok = server.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "list_accounts", "arguments": {}}}
    )
    assert ok["result"]["content"][0]["type"] == "text"
    assert "Giro" in ok["result"]["content"][0]["text"]

    unknown = server.handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}}
    )
    assert unknown["error"]["code"] == -32602

    bad_args = server.handle_message(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "list_accounts", "arguments": {"bogus": 1}}}
    )
    assert bad_args["error"]["code"] == -32602

    no_arguments_key = server.handle_message(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "get_status"}}
    )
    assert '"account_count": 1' in no_arguments_key["result"]["content"][0]["text"]


def test_list_accounts_masks_on_request(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_accounts", lambda **k: [{"iban": "DE001234", "name": "G"}]
    )
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)
    assert _parse(server.list_accounts())["accounts"][0]["iban"] == "DE001234"
    assert _parse(server.list_accounts(mask_ids=True))["accounts"][0]["iban"] == "DE**1234"
    monkeypatch.setenv(privacy.ENV_VAR, "1")
    assert _parse(server.list_accounts())["accounts"][0]["iban"] == "DE**1234"


def test_get_transactions_masks_rows(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_transactions", lambda **k: [{"accountNumber": "987654"}]
    )
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)
    body = _parse(server.get_transactions(mask_ids=True))
    assert body["transactions"][0]["accountNumber"] == "98**54"


def test_list_statements_truncates_and_strips_paths(monkeypatch):
    entries = [
        {"account": "A", "name": f"{i}.pdf", "size": 1, "modified": "2026-01-01",
         "path": "/secret/x.pdf"}
        for i in range(3)
    ]
    monkeypatch.setattr(statements, "list_statements", lambda **k: entries)
    body = _parse(server.list_statements(limit=2))
    assert body["total_count"] == 3
    assert body["truncated"] is True
    assert "path" not in body["statements"][0]
    assert server.list_statements(limit=0).startswith("Error:")


def test_get_statement_masks_text_and_reveal_wins(monkeypatch):
    entry = {
        "account": "A", "name": "x.pdf", "size": 1, "modified": "2026-01-01",
        "path": "/secret/x.pdf", "text": "IBAN DE89370400440532013000",
        "text_truncated": False, "extraction": "full",
    }
    monkeypatch.setattr(statements, "get_statement", lambda *a, **k: dict(entry))
    monkeypatch.delenv(privacy.ENV_VAR, raising=False)
    plain = _parse(server.get_statement("A", "x.pdf"))
    assert "DE89370400440532013000" in plain["text"]
    assert "path" not in plain
    assert "DE****" in _parse(server.get_statement("A", "x.pdf", mask_ids=True))["text"]
    monkeypatch.setenv(privacy.ENV_VAR, "1")
    assert "DE****" in _parse(server.get_statement("A", "x.pdf"))["text"]
    revealed = _parse(server.get_statement("A", "x.pdf", mask_ids=False))
    assert "DE89370400440532013000" in revealed["text"]


def test_get_statement_surfaces_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise statements.StatementsError("nope")

    monkeypatch.setattr(statements, "get_statement", boom)
    assert server.get_statement("A", "x.pdf").startswith("Error: nope")


def test_fetch_rows_joins_account_names(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_transactions", lambda **k: [{"accountUuid": "u1"}]
    )
    monkeypatch.setattr(
        bridge, "export_accounts", lambda **k: [{"uuid": "u1", "name": "Giro"}]
    )
    body = _parse(server.get_transactions(from_date="2026-01-01"))
    assert body["transactions"][0]["account"] == "Giro"


def test_joins_never_overwrite_existing_keys(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "export_transactions",
        lambda **k: [{"accountUuid": "u1", "account": "orig",
                      "categoryUuid": "c1", "category": "orig",
                      "category_path": "orig"}],
    )
    monkeypatch.setattr(
        bridge, "export_accounts", lambda **k: [{"uuid": "u1", "name": "Giro"}]
    )
    monkeypatch.setattr(
        bridge, "export_categories",
        lambda **k: [{"uuid": "c1", "name": "Food", "indentation": 0}],
    )
    row = _parse(server.get_transactions(from_date="2026-01-01"))["transactions"][0]
    assert (row["account"], row["category"], row["category_path"]) == (
        "orig", "orig", "orig")


def test_fetch_rows_tolerates_account_lookup_failure(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_transactions", lambda **k: [{"accountUuid": "u1"}]
    )

    def boom(**kwargs):
        raise bridge.MoneyMoneyError("down")

    monkeypatch.setattr(bridge, "export_accounts", boom)
    body = _parse(server.get_transactions(from_date="2026-01-01"))
    assert "account" not in body["transactions"][0]


def test_cashflow_timeline_reports_periods(monkeypatch):
    rows = [
        {"bookingDate": "2026-01-10", "amount": 100, "currency": "EUR"},
        {"bookingDate": "2026-01-20", "amount": -40, "currency": "EUR"},
    ]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: rows)
    body = _parse(server.cashflow_timeline(from_date="2026-01-01", to_date="2026-01-31"))
    assert body["periods"] == [
        {"period": "2026-01", "count": 2, "income": {"EUR": 100.0},
         "expenses": {"EUR": -40.0}, "net": {"EUR": 60.0}}
    ]
    assert server.cashflow_timeline(freq="daily").startswith("Error:")


def test_get_budgets_defaults_to_current_month(monkeypatch):
    cats = [{"uuid": "a", "name": "Food", "currency": "EUR",
             "budget": {"amount": 500, "available": 200, "period": "month"}}]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: [])
    monkeypatch.setattr(bridge, "export_categories", lambda **k: cats)
    body = _parse(server.get_budgets())
    assert body["from_date"] == date.today().replace(day=1).isoformat()
    assert body["budgets"][0]["path"] == "Food"
    assert body["budgets"][0]["spent"] == {}
    monkeypatch.setattr(bridge, "export_categories", lambda **k: {"oops": 1})
    assert server.get_budgets().startswith("Error:")


def test_dispatch_rejects_garbage():
    assert server.handle_message([1, 2]) is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "nope/x"}) is None
    missing = server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "nope/x"})
    assert missing["error"]["code"] == -32601
    no_method = server.handle_message({"jsonrpc": "2.0", "id": 8})
    assert no_method["error"]["code"] == -32600
    bad_params = server.handle_message(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": ["x"]}
    )
    assert bad_params["error"]["code"] == -32600


def test_get_status_reports_unlocked_with_count(monkeypatch):
    monkeypatch.setattr(bridge, "export_accounts", lambda **k: [{}, {}])
    assert _parse(server.get_status()) == {
        "moneymoney_running": True,
        "database_unlocked": True,
        "account_count": 2,
    }


def test_get_status_reports_locked(monkeypatch):
    def locked(**kwargs):
        raise bridge.DatabaseLockedError()

    monkeypatch.setattr(bridge, "export_accounts", locked)
    assert _parse(server.get_status()) == {
        "moneymoney_running": True,
        "database_unlocked": False,
    }


def test_get_status_reports_not_running(monkeypatch):
    def gone(**kwargs):
        raise bridge.MoneyMoneyError("Application isn't running")

    monkeypatch.setattr(bridge, "export_accounts", gone)
    body = _parse(server.get_status())
    assert body["moneymoney_running"] is False


def _tx(name, amount, currency="EUR", date="2026-02-15", purpose="ref", **extra):
    return {
        "name": name,
        "amount": amount,
        "currency": currency,
        "bookingDate": date,
        "purpose": purpose,
        **extra,
    }


def test_search_transactions_filters_text_and_amount(monkeypatch):
    rows = [
        _tx("FreshMart Market", -40, purpose="groceries"),
        _tx("FuelStop", -50, purpose="fuel"),
        _tx("FreshMart Express", -5, purpose="snack"),
    ]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: rows)
    body = _parse(server.search_transactions(counterparty="freshmart", min_amount=-10))
    assert body["matched_count"] == 1
    assert body["transactions"][0]["name"] == "FreshMart Express"
    body = _parse(server.search_transactions(purpose="FUEL"))
    assert body["matched_count"] == 1
    body = _parse(server.search_transactions(max_amount=-45))
    assert body["matched_count"] == 1


def test_top_counterparties_ranks_and_converts(monkeypatch):
    rows = [_tx("A-shop", -10), _tx("A-shop", -10), _tx("Big", -100, "USD")]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: rows)
    monkeypatch.setattr(
        fx, "get_rates", lambda: fx.RateTable(rates={"USD": 1.1}, as_of="d", stale=False)
    )
    body = _parse(server.top_counterparties(n=5))
    assert [c["name"] for c in body["counterparties"]] == ["A-shop", "Big"]
    body = _parse(server.top_counterparties(n=5, convert_to="EUR"))
    assert [c["name"] for c in body["counterparties"]] == ["Big", "A-shop"]
    assert body["counterparties"][0]["converted_total"] == pytest.approx(-90.91, abs=0.01)
    assert body["unconverted_currencies"] == []


def test_top_counterparties_reports_unconverted(monkeypatch):
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: [_tx("x", -5, "VND")])
    monkeypatch.setattr(
        fx, "get_rates", lambda: fx.RateTable(rates={"USD": 1.1}, as_of="d", stale=True)
    )
    body = _parse(server.top_counterparties(convert_to="EUR"))
    assert body["unconverted_currencies"] == ["VND"]
    assert body["rates_stale"] is True


def test_top_counterparties_survives_fx_outage(monkeypatch):
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: [_tx("x", -5)])
    monkeypatch.setattr(
        fx, "get_rates", lambda: (_ for _ in ()).throw(fx.FxError("offline"))
    )
    body = _parse(server.top_counterparties(convert_to="EUR"))
    assert "fx_error" in body
    assert body["counterparties"][0]["name"] == "x"


def test_get_recurring_passes_through(monkeypatch):
    rows = [_tx("Sub", -9, date=f"2026-0{m}-01") for m in range(1, 5)]
    monkeypatch.setattr(bridge, "export_transactions", lambda **k: rows)
    body = _parse(server.get_recurring())
    assert body["subscriptions"][0]["name"] == "Sub"
    assert server.get_recurring(min_occurrences=1).startswith("Error:")


def test_compare_periods_merges(monkeypatch):
    calls = []

    def fake_tx(from_date, to_date=None, **kwargs):
        calls.append((from_date, to_date))
        return [_tx("s", -1, category="Food")] if to_date == "2026-01-31" else [_tx("s", -2)]

    monkeypatch.setattr(bridge, "export_transactions", fake_tx)
    body = _parse(
        server.compare_periods(
            from_a="2026-01-01", to_a="2026-01-31", from_b="2026-02-01", to_b="2026-02-28"
        )
    )
    assert body["count_a"] == 1 and body["count_b"] == 1
    assert body["totals_a"] == {"EUR": -1.0}
    assert server.compare_periods("a", "b", "c", "d", group_by="x").startswith("Error:")


def test_get_category_total_with_conversion(monkeypatch):
    monkeypatch.setattr(
        bridge, "export_transactions", lambda **k: [_tx("s", -110, "USD", category="Food")]
    )
    monkeypatch.setattr(
        fx, "get_rates", lambda: fx.RateTable(rates={"USD": 1.1}, as_of="d", stale=False)
    )
    body = _parse(
        server.get_category_total(prefix="Food", convert_to="EUR")
    )
    assert body["count"] == 1
    assert body["converted_total"] == pytest.approx(-100.0)
    assert server.get_category_total().startswith("Error:")
    assert server.get_category_total(category="a", prefix="b").startswith("Error:")
