"""IBAN / account-number masking for AI-bound output.

Counterparty names and purposes are content and stay verbatim; this module
covers identifiers. Masking keeps the first 2 and last 4 characters so
masked values stay correlatable across calls within one session.
"""

from __future__ import annotations

import os
import re
from typing import Any

ENV_VAR = "MONEYMONEY_MASK_IDS"

# Grouped 4-char blocks (printed form); case-insensitive country only, so a
# lowercase word after an IBAN can never be swallowed into the candidate.
_IBAN_CANDIDATE = re.compile(
    r"\b(?i:[A-Z]{2})\d{2}(?: ?[A-Z0-9]{4}){2,7} ?[A-Z0-9]{0,4}\b"
)


def masking_enabled(explicit: bool | None = None) -> bool:
    """Per-call flag wins; otherwise the env var decides (default off)."""
    if explicit is not None:
        return explicit
    return os.environ.get(ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def mask_id(value: Any) -> Any:
    """Mask an IBAN/account number. Integers (plist account numbers) are
    masked as strings; anything else non-string is left untouched."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    if len(value) <= 6:
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
    return value[:2] + "*" * (len(value) - 6) + value[-4:]


def mask_account(account: dict[str, Any]) -> dict[str, Any]:
    masked = dict(account)
    for key in ("iban", "accountNumber"):
        if key in masked:
            masked[key] = mask_id(masked[key])
    return masked


def mask_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    masked = dict(tx)
    if "accountNumber" in masked:
        masked["accountNumber"] = mask_id(masked["accountNumber"])
    return masked


def is_valid_iban(value: str) -> bool:
    """ISO 13616 mod-97 check. Only valid IBANs are masked in free text."""
    compact = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    return int(digits) % 97 == 1


def mask_text(text: str) -> str:
    """Mask valid IBANs inside free text (statement bodies)."""

    def scrub(match: re.Match) -> str:
        candidate = match.group(0)
        if not is_valid_iban(candidate):
            return candidate
        compact = re.sub(r"\s+", "", candidate).upper()
        return mask_id(compact)

    return _IBAN_CANDIDATE.sub(scrub, text)
