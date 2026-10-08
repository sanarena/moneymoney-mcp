"""Access to MoneyMoney's electronic bank statements on disk.

MoneyMoney downloads electronic statements (PDFs) into
``.../MoneyMoney/Statements/<account>/``. There is no AppleScript API for
them, so this module reads the files directly (read-only) and extracts
text with the stdlib PDF reader.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from moneymoney_mcp import pdftext

ENV_VAR = "MONEYMONEY_STATEMENTS_DIR"
DEFAULT_MAX_CHARS = 8000


class StatementsError(Exception):
    """Statements are unavailable or the request was invalid."""


def base_dir() -> Path:
    custom = os.environ.get(ENV_VAR)
    if custom:
        return Path(custom)
    return (
        Path.home()
        / "Library"
        / "Containers"
        / "com.moneymoney-app.retail"
        / "Data"
        / "Library"
        / "Application Support"
        / "MoneyMoney"
        / "Statements"
    )


def _stat_dict(account: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "account": account,
        "name": path.name,
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).date().isoformat(),
        "path": str(path),
    }


def list_statements(
    account: str | None = None, since: str | None = None
) -> list[dict[str, Any]]:
    """List statement files, newest first. ``since`` is YYYY-MM-DD (mtime)."""
    base = base_dir()
    if since is not None:
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise StatementsError(f"since must be YYYY-MM-DD, got {since!r}.") from None
    if account is not None:
        account = str(account)
    if not base.is_dir():
        return []
    found: list[dict[str, Any]] = []
    try:
        folders = sorted(base.iterdir())
    except OSError as exc:
        raise StatementsError(f"Could not list statements: {exc}.") from exc
    for folder in folders:
        if not folder.is_dir():
            continue
        if account and folder.name.lower() != account.lower():
            continue
        try:
            paths = sorted(folder.iterdir())
        except OSError:
            continue
        for path in paths:
            if not path.is_file():
                continue
            entry = _stat_dict(folder.name, path)
            if since and entry["modified"] < since:
                continue
            found.append(entry)
    found.sort(key=lambda e: e["name"])
    found.sort(key=lambda e: e["modified"], reverse=True)
    return found


def get_statement(
    account: str, name: str, max_chars: int = DEFAULT_MAX_CHARS
) -> dict[str, Any]:
    """Read one statement file with best-effort text extraction."""
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError, OverflowError):
        raise StatementsError(f"max_chars must be an integer, got {max_chars!r}.") from None
    if max_chars < 1:
        raise StatementsError(f"max_chars must be >= 1, got {max_chars}.")
    base = base_dir()
    candidate = (base / str(account) / str(name)).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        raise StatementsError(f"Invalid statement path: {account}/{name}.") from None
    if not candidate.is_file():
        raise StatementsError(f"Statement not found: {account}/{name}.")
    entry = _stat_dict(account, candidate)
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise StatementsError(f"Could not read statement: {exc}.") from exc
    if candidate.suffix.lower() != ".pdf":
        entry.update({"text": "", "text_truncated": False, "extraction": "unavailable"})
        return entry
    try:
        result = pdftext.extract(raw)
    except Exception as exc:
        raise StatementsError(f"Could not parse statement PDF: {exc}.") from exc
    text = result.text
    entry.update(
        {
            "text": text[:max_chars],
            "text_truncated": len(text) > max_chars,
            "extraction": result.status,
        }
    )
    return entry
