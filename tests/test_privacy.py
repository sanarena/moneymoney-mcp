"""Tests for IBAN / account-number masking."""

from __future__ import annotations

from moneymoney_mcp import privacy


def test_mask_id_shapes():
    assert privacy.mask_id("DE89370400440532013000") == "DE****************3000"
    assert privacy.mask_id("123456") == "12**56"
    assert privacy.mask_id("1234") == "****"
    assert privacy.mask_id("") == ""
    assert privacy.mask_id(None) is None
    assert privacy.mask_id(1234567890) == "12****7890"
    assert privacy.mask_id("************4242") == "************4242"


def test_is_valid_iban_checks_mod97():
    assert privacy.is_valid_iban("DE89370400440532013000") is True
    assert privacy.is_valid_iban("GB29 NWBK 6016 1331 9268 19") is True
    assert privacy.is_valid_iban("DE00370400440532013000") is False
    assert privacy.is_valid_iban("DE12 SHORT") is False
    assert privacy.is_valid_iban("") is False


def test_mask_text_only_masks_valid_ibans():
    text = "Pay DE89370400440532013000 or DE00370400440532013000 now"
    assert privacy.mask_text(text) == (
        "Pay DE****************3000 or DE00370400440532013000 now"
    )
    assert privacy.mask_text("no numbers here") == "no numbers here"
    assert privacy.mask_text("iban de89370400440532013000!") == (
        "iban DE****************3000!"
    )
    assert privacy.mask_id(True) is True


def test_masking_enabled_prefers_explicit(monkeypatch):
    monkeypatch.setenv(privacy.ENV_VAR, "true")
    assert privacy.masking_enabled() is True
    assert privacy.masking_enabled(False) is False
    monkeypatch.delenv(privacy.ENV_VAR)
    assert privacy.masking_enabled() is False
    assert privacy.masking_enabled(True) is True


def test_record_helpers_copy_and_mask_only_ids():
    account = {"name": "Giro", "iban": "DE001234", "balance": 1.0}
    masked = privacy.mask_account(account)
    assert masked["iban"] == "DE**1234"
    assert account["iban"] == "DE001234"
    tx = {"name": "x", "accountNumber": "987654", "purpose": "keep me"}
    masked_tx = privacy.mask_transaction(tx)
    assert masked_tx["accountNumber"] == "98**54"
    assert masked_tx["purpose"] == "keep me"
