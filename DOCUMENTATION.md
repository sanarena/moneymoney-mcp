# moneymoney-mcp — Full Documentation

Complete reference for the read-only MCP server for
[MoneyMoney](https://moneymoney.app/). Start with [README.md](README.md)
for the 2-minute setup.

1. [Architecture](#1-architecture)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Tool reference](#4-tool-reference)
5. [Analytics in depth](#5-analytics-in-depth)
6. [Statements](#6-statements)
7. [Privacy & security](#7-privacy--security)
8. [Lua companion extension](#8-lua-companion-extension)
9. [Operations](#9-operations)
10. [Troubleshooting/FAQ](#10-troubleshootingfaq)
11. [Development](#11-development)
12. [Related projects](#12-related-projects)

## 1. Architecture

```text
AI assistant  <--- MCP over stdio (JSON-RPC) --->  server.py  <--- AppleScript --->  MoneyMoney.app
                                                                     |
                                              Statements folder <----+ (read from disk)
```

Why this shape:

- MoneyMoney's SQLite database is **AES-encrypted** — it cannot be read
  directly. The only supported outside access is the official
  [AppleScript API](https://moneymoney.app/api/applescript/) (`export
  accounts/categories/transactions/portfolio … as "plist"`).
- A `.lua` file inside MoneyMoney can only run when the app invokes it, so
  it cannot host the persistent process MCP needs. Hence a small external
  server, plus an optional Lua export for one-click use.
- The server is **stdlib-only, zero dependencies**: it speaks MCP JSON-RPC
  directly instead of via an SDK, so it runs on stock macOS
  `/usr/bin/python3` (3.9+) with nothing to install.

Project layout:

```text
src/moneymoney_mcp/
  server.py      14 MCP tools + stdio JSON-RPC transport
  bridge.py      osascript runner, plist parsing, error mapping
  analytics.py   pure aggregations (counterparties, recurring, compare, …)
  fx.py          ECB euro reference rates with 12h file cache
  statements.py  statement listing/reading from disk
  pdftext.py     stdlib PDF text extractor
  privacy.py     IBAN masking + reveal logic
extensions/AIChatExport.lua   one-click Markdown export for MoneyMoney
tests/           pytest suite (mocked) + lua_harness.lua
```

## 2. Requirements

- macOS with MoneyMoney installed
- MoneyMoney **running with its database unlocked** (all AppleScript tools;
  statements work even while locked — see §6)
- Python 3.9+ (stock `/usr/bin/python3` is fine)
- An MCP-capable AI client

## 3. Installation

```sh
git clone https://github.com/sanarena/moneymoney-mcp
```

No `pip install`, no venv, no build. Point the client at `server.py`:

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```jsonc
{ "mcpServers": { "moneymoney": {
  "command": "/usr/bin/python3",
  "args": ["/absolute/path/to/moneymoney-mcp/src/moneymoney_mcp/server.py"] } } }
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.moneymoney]
command = "/usr/bin/python3"
args = ["/absolute/path/to/moneymoney-mcp/src/moneymoney_mcp/server.py"]
enabled = true
```

**Cursor** (`~/.cursor/mcp.json`): same `command`/`args` under `"mcpServers"`.

**Any other client**: stdio server, same command + args.

Optional environment (set in the client's `env` map):

| Variable | Effect |
|---|---|
| `MONEYMONEY_MASK_IDS=1` | Mask IBANs/account numbers by default (per-call `mask_ids: false` reveals) |
| `MONEYMONEY_STATEMENTS_DIR=…` | Override the statements folder location |

Python users may alternatively `pip install .` and use the `moneymoney-mcp`
console script as the command. The package is also on PyPI as
[`moneymoney-mcp`](https://pypi.org/p/moneymoney-mcp) (`uvx moneymoney-mcp`).

## 4. Tool reference

Conventions: dates are `YYYY-MM-DD`. `account` accepts a UUID, IBAN,
number, name or group name. `category` accepts a UUID or name, nested as
`"Food\Groceries"`. Every transaction carries resolved `category`,
`category_path` and `account` names alongside the raw UUIDs. Amounts keep
full precision (crypto-safe to 8 decimals); totals are always per currency
unless `convert_to` is given.

| Tool | Key params | Returns |
|---|---|---|
| `get_status` | — | app running? DB unlocked? account count |
| `list_accounts` | `mask_ids?` | all accounts + balances |
| `list_categories` | — | all categories (flat, with indentation) |
| `get_transactions` | `from_date?` (def. 90d), `to_date?`, `account?`, `category?`, `limit?` (def. 200, max 2000), `mask_ids?` | txs newest-first + `total_count`/`truncated` |
| `search_transactions` | above + `counterparty?`, `purpose?` (substrings), `min_amount?`, `max_amount?` | like above + `matched_count` |
| `get_portfolio` | `account?` | positions: name, quantity, price, amount, currencies, trade date |
| `top_counterparties` | dates?, `account?`, `n?` (def. 10), `convert_to?` | ranked payees + per-currency totals |
| `get_recurring` | dates?, `account?`, `min_occurrences?` (def. 3) | subscription clusters + cadence |
| `compare_periods` | `from_a`, `to_a`, `from_b`, `to_b` (required), `account?`, `group_by?` | side-by-side counts + totals |
| `get_category_total` | `category?` **xor** `prefix?`, dates?, `account?`, `convert_to?` | sum + count, subtree-aware |
| `cashflow_timeline` | dates?, `account?`, `freq?` (monthly/quarterly/yearly), `convert_to?` | income/expenses/net per period |
| `get_budgets` | `from_date?` (def. current month), `to_date?`, `convert_to?` | budgets vs actual spend |
| `list_statements` | `account?`, `since?`, `limit?` (def. 100) | statement files, newest first |
| `get_statement` | `account`, `name` (required), `max_chars?` (def. 8000), `mask_ids?` | metadata + extracted text + quality |

## 5. Analytics in depth

All aggregations run server-side (exact sums, minimal context):

- **Counterparties** rank by transaction count, or by converted absolute
  total when `convert_to` is given.
- **Recurring detection** clusters by counterparty + currency + stable
  amount (±5%, min 1.0 — no chained drift), requires 3+ charges on 2+
  distinct dates, and labels cadence from the median interval
  (weekly/biweekly/monthly/quarterly/semiannual/yearly/irregular) with a
  `next_expected_date`.
- **Category matching** is case-insensitive and segment-aware: prefix
  `"Bus"` does not match `"Business"`. Full paths (`Food > Groceries`)
  are reconstructed from the export's indentation levels.
- **Budgets**: MoneyMoney reports each budget as
  `{amount, available, period}`; the tool passes it through untouched and
  adds our own spend (expenses only, whole subtree) over your range.
  `available` is MoneyMoney's figure for its current period; `spent`
  covers your queried range — both are shown so they can't be confused.
- **Currency conversion** uses ECB euro reference rates (≈30 currencies,
  12h cache in `~/.cache/moneymoney-mcp/`). Anything outside coverage
  (e.g. VND) is listed under `unconverted_currencies`, never guessed.
  Stale-cache fallback is flagged via `rates_stale`.

## 6. Statements

MoneyMoney downloads electronic statements (PDF) per account. There is no
AppleScript API for them, so they are read straight from disk — which also
means **statement tools work while the database is locked**.

Text extraction is stdlib-only (FlateDecode, literal/hex strings, `Tj`/`TJ`
with positioning-aware spacing, per-font ToUnicode CMaps incl. `bfchar`/
`bfrange`, compressed object streams). Every result reports honesty-first
quality: `full` (everything mapped), `partial` (some glyphs unmapped),
`unavailable` (no readable text — a Unicode-category gate rejects
mojibake instead of flooding context). Validated against 149 real
Trade Republic PDFs: zero crashes.

`get_statement` caps text at `max_chars` (default 8000, `text_truncated`
flag) and never returns the on-disk path. Path traversal (`..`, absolute
paths) is rejected.

## 7. Privacy & security

- **Read-only by architecture**: the server only issues `export …`
  commands. It cannot move, change, categorize, or create anything.
- **Local-first**: everything runs on your Mac. Data leaves it only via
  your own chats with your AI provider — plus an ECB rate download when
  you use `convert_to`.
- **No credentials**: your MoneyMoney password is never handled, stored,
  or needed by this project. Unlock the app yourself.
- **Optional masking** (off by default): `mask_ids: true` per call or
  `MONEYMONEY_MASK_IDS=1` server-wide masks IBANs/account numbers
  (first 2 + last 4 kept, correlatable, idempotent). Statement text gets
  mod-97-validated IBAN scrubbing (order numbers and lookalikes are never
  touched). Explicit `mask_ids: false` reveals again — "reveal wins".
  UUIDs stay visible so follow-up filters keep working.

### ⚠️ Transaction texts are third-party input

Counterparty names and purpose/reference texts come back verbatim — and
they are written by *whoever pays you*. A 1-cent transfer can plant
arbitrary text (including fake instructions to a language model) that
your assistant reads at the next query. This server alone cannot move
money; your risk depends on which *other* tools share the session. There
is no server-side fix that preserves the data (the purpose *is* the
content), so treat these fields as untrusted input when acting on them.
(Credit to [Schimmilab/moneymoney-mcp-server](https://github.com/Schimmilab/moneymoney-mcp-server)
for articulating this.)

## 8. Lua companion extension

`extensions/AIChatExport.lua` adds an **AI Chat (Markdown)** format to
*Account → Export Transactions*: one click exports the account as compact
Markdown (header + chronological table) for pasting into any chatbot —
no MCP client needed.

Install: in MoneyMoney open *Help → Show Database in Finder*, copy the
file into `Extensions`. Allow unsigned community extensions once under
*Settings → Extensions* (restart the app afterwards).

Headless trigger (note: AppleScript wants the file's base name, not the
display name):

```applescript
tell application "MoneyMoney" to export transactions ¬
    from date "2026-01-01" to date "2026-12-31" as "AIChatExport"
```

Crypto-safe amounts (8 decimals, trimmed) and multi-currency balances
are handled. Smoke-tested in-repo, no MoneyMoney needed:

```sh
lua tests/lua_harness.lua extensions/AIChatExport.lua
```

## 9. Operations

MoneyMoney must be **running and unlocked** for every AppleScript-backed
tool (everything except statements). Practical setup: keep it running
(it idles fine), relax the auto-lock timeout under Settings → Security,
optionally add it to Login Items. When locked, tools answer
`MoneyMoney database is locked…` — unlock and re-ask.

## 10. Troubleshooting/FAQ

- **`database is locked`** → unlock MoneyMoney, re-ask.
- **Tools missing in the client** → restart the AI client (config loads
  at launch); check the `server.py` path is absolute.
- **Empty results** → check `account`/`category` spelling via
  `list_accounts`/`list_categories`; widen the date range.
- **`unconverted_currencies: ["VND"]`** → outside ECB coverage; totals
  stay per currency, nothing is guessed.
- **Statement `extraction: unavailable`** → the PDF has no mappable text
  (e.g. scanned); metadata is still returned.
- **Can it move money / categorize / edit?** No — read-only.
- **Which Python?** Any 3.9+, including stock `/usr/bin/python3`.
- **ChatGPT app?** It only accepts remote HTTPS connectors and cannot
  launch local processes, so this stdio server doesn't plug into it
  directly. Claude Desktop, Codex, Cursor and other stdio-capable
  clients work.

## 11. Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q
```

104 tests, all mocked — no MoneyMoney, network, or statements needed.
The PDF extractor is additionally validated against real-world PDFs in
CI-less local runs (149 files, zero crashes). Keep it stdlib-only.

## 12. Related projects

Small pond, no canonical implementation yet — all AppleScript-based:

- [lukasmalkmus/moneymoney](https://github.com/lukasmalkmus/moneymoney)
  (Rust) — CLI + MCP + plugins, most complete, includes write tools.
- [AndreasDietzel/moneymoney-mcp-server](https://github.com/AndreasDietzel/moneymoney-mcp-server)
  (TypeScript) — 14 tools incl. mock-data dev mode.
- [pipamann/moneymoney-mcp](https://github.com/pipamann/moneymoney-mcp)
  (TypeScript) — on npm, includes transfers.
- [alemuenchen/moneymoney-mcp](https://github.com/alemuenchen/moneymoney-mcp)
  (TypeScript) — richest analytics incl. writes (gated); budget-field
  semantics referenced from its types.
- [Schimmilab/moneymoney-mcp-server](https://github.com/Schimmilab/moneymoney-mcp-server)
  (Python) — read-only, single file; prompt-injection warning adopted.

This project differentiates: strict read-only, stdlib-only zero-install
(stock Python 3.9), test suite, FX conversion, cadence-labeled
subscriptions, cash-flow/budgets, statement text extraction with quality
reporting, IBAN masking with reveal, and a Lua companion export.

## License

MIT — see [LICENSE](LICENSE). Changes: [CHANGELOG.md](CHANGELOG.md).
