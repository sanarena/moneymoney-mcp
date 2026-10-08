"""Tests for statement listing/reading (temporary directories only)."""

from __future__ import annotations

import os
import time

import pytest

from moneymoney_mcp import statements

TINY_PDF = (
    b"%PDF-1.5\n1 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"2 0 obj\n<< /Type /Page /Resources << /Font << /F1 1 0 R >> >> /Contents 3 0 R >>\nendobj\n"
    b"3 0 obj\n<< /Length 30 >>\nstream\nBT /F1 12 Tf (Hi) Tj ET\nendstream\nendobj\n"
)


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setenv(statements.ENV_VAR, str(tmp_path))
    ing = tmp_path / "ING"
    ing.mkdir()
    (ing / "2026-01.pdf").write_bytes(TINY_PDF)
    (ing / "notes.txt").write_text("x")
    old = ing / "2025-01.pdf"
    old.write_bytes(TINY_PDF)
    past = time.time() - 400 * 86400
    os.utime(old, (past, past))
    (tmp_path / "N26").mkdir()
    return tmp_path


def test_list_all_newest_first(base):
    out = statements.list_statements()
    assert [e["name"] for e in out] == ["2026-01.pdf", "notes.txt", "2025-01.pdf"]
    assert out[0]["account"] == "ING"
    assert out[0]["size"] == len(TINY_PDF)


def test_list_filters_account_and_since(base):
    assert statements.list_statements(account="ing") != []
    assert statements.list_statements(account="nope") == []
    out = statements.list_statements(since="2026-01-01")
    assert {e["name"] for e in out} == {"2026-01.pdf", "notes.txt"}
    with pytest.raises(statements.StatementsError, match="YYYY-MM-DD"):
        statements.list_statements(since="yesterday")


def test_list_tolerates_non_string_filters(base):
    assert statements.list_statements(account=123) == []
    with pytest.raises(statements.StatementsError, match="YYYY-MM-DD"):
        statements.list_statements(since=20260101)


def test_get_statement_coerces_max_chars(base):
    out = statements.get_statement("ING", "2026-01.pdf", max_chars="1")
    assert out["text"] == "H"
    with pytest.raises(statements.StatementsError, match="integer"):
        statements.get_statement("ING", "2026-01.pdf", max_chars="lots")


def test_list_missing_dir_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(statements.ENV_VAR, str(tmp_path / "absent"))
    assert statements.list_statements() == []


def test_get_statement_reads_text_and_truncates(base):
    out = statements.get_statement("ING", "2026-01.pdf")
    assert out["text"] == "Hi"
    assert out["extraction"] == "full"
    assert out["text_truncated"] is False
    out = statements.get_statement("ING", "2026-01.pdf", max_chars=1)
    assert out["text"] == "H"
    assert out["text_truncated"] is True
    with pytest.raises(statements.StatementsError, match="max_chars"):
        statements.get_statement("ING", "2026-01.pdf", max_chars=0)


def test_get_statement_non_pdf_has_no_text(base):
    out = statements.get_statement("ING", "notes.txt")
    assert out["extraction"] == "unavailable"
    assert out["text"] == ""


def test_get_statement_rejects_traversal_and_missing(base):
    with pytest.raises(statements.StatementsError, match="Invalid"):
        statements.get_statement("..", "x.pdf")
    with pytest.raises(statements.StatementsError, match="Invalid"):
        statements.get_statement("ING", "../../x.pdf")
    with pytest.raises(statements.StatementsError, match="not found"):
        statements.get_statement("ING", "absent.pdf")
