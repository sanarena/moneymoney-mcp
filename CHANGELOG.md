# Changelog

## 0.4.1

- Docs only: PyPI one-liner as the primary install path (same code as 0.4.0).

## 0.4.0

- New tools: `cashflow_timeline` (income/expenses/net per period),
  `get_budgets` (budgets vs actual spend, subtree-aware).
- Transactions now resolve `account` names alongside categories.

## 0.3.0

- New tools: `list_statements`, `get_statement` (stdlib PDF text extraction
  with full/partial/unavailable quality reporting + mojibake gate).
- IBAN/account-number masking via `mask_ids` param or `MONEYMONEY_MASK_IDS=1`,
  including mod-97-validated IBAN scrubbing in statement text; explicit
  `mask_ids: false` reveals full numbers even when masked by default.
- Robustness QA: locale-proof AppleScript decoding, strict locked-DB
  detection, defensive MCP/dispatch handling, subscription `next_expected_date`,
  crypto-safe Lua amounts, committed Lua smoke harness.

## 0.2.0

- Stdlib-only: dropped the MCP SDK, speak MCP over stdio directly.
  Zero dependencies, runs on stock macOS Python 3.9+.
- New tools: `get_status`, `search_transactions`, `top_counterparties`,
  `get_recurring` (with cadence labels), `compare_periods`,
  `get_category_total`.
- ECB currency conversion via `convert_to`; uncovered pairs reported as
  `unconverted_currencies`, never guessed.
- Category UUIDs resolve to full paths (`Food > Groceries`).
- README: prompt-injection security note.

## 0.1.0

- Initial release: read-only `list_accounts`, `list_categories`,
  `get_transactions`, `get_portfolio` via AppleScript, plus the
  `AIChatExport.lua` companion export extension.
