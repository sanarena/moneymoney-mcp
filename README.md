# moneymoney-mcp

Chat with your MoneyMoney data. This is a **read-only** MCP server for
[MoneyMoney](https://moneymoney.app/) (the macOS banking app) that lets any
AI assistant answer questions like:

- "How much did I spend on groceries last quarter?"
- "What are my current balances?"
- "Which subscriptions do I pay for?"
- "September vs August — where did the money go?"

No installation needed: it runs on the Python macOS already ships with,
and speaks to MoneyMoney through its official AppleScript API.

> Unofficial community project, not affiliated with MoneyMoney (MRH
> applications GmbH). Full reference: [DOCUMENTATION.md](DOCUMENTATION.md).

## Requirements

- macOS with MoneyMoney installed, running, database unlocked
- Any MCP-capable AI client (Claude Desktop, Codex, Cursor, …)

## Install (2 minutes)

**Option A — one line, no clone (recommended).** Needs
[`uv`](https://docs.astral.sh/uv/) (`brew install uv`):

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```jsonc
{ "mcpServers": { "moneymoney": {
  "command": "uvx",
  "args": ["moneymoney-mcp"] } } }
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.moneymoney]
command = "uvx"
args = ["moneymoney-mcp"]
```

Any other client: same idea — a stdio server with command `uvx` and args
`["moneymoney-mcp"]`. (`pipx run moneymoney-mcp` or `pip install
moneymoney-mcp` + command `moneymoney-mcp` work too.)

**Option B — clone, zero dependencies.** No `uv`, no `pip`, stock macOS
Python is enough:

```sh
git clone https://github.com/sanarena/moneymoney-mcp
```

then use command `/usr/bin/python3` with args
`["/absolute/path/to/moneymoney-mcp/src/moneymoney_mcp/server.py"]`.

Then restart the AI client. If MoneyMoney's database is locked you'll get
a clear `database is locked` message — unlock the app and ask again.

## What the AI can do

14 read-only tools: accounts, balances, transactions (search/filter),
categories, portfolio, counterparties, subscriptions, period comparison,
category totals, cash-flow timeline, budgets, and bank statements.
See the [full tool reference](DOCUMENTATION.md#4-tool-reference).

Prefer no AI client? Copy `extensions/AIChatExport.lua` into MoneyMoney's
Extensions folder for one-click Markdown export to paste into any chatbot.
([Details](DOCUMENTATION.md#8-lua-companion-extension))

## Privacy

- Local only; nothing leaves your Mac except what you send your AI provider
- Read-only by design — it cannot move, change, or create anything
- Optional IBAN masking (`mask_ids`, or `MONEYMONEY_MASK_IDS=1`); pass
  `mask_ids: false` to reveal again
- Your MoneyMoney password is never touched

Details + the prompt-injection warning:
[DOCUMENTATION.md](DOCUMENTATION.md#7-privacy--security).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `MoneyMoney database is locked` | Open MoneyMoney, unlock, re-ask |
| Tools missing in client | Restart the AI client after config change |
| `File not found` at startup | Check the `server.py` path is absolute |

More: [DOCUMENTATION.md](DOCUMENTATION.md#10-troubleshootingfaq).

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q        # 104 tests, no MoneyMoney needed
lua tests/lua_harness.lua extensions/AIChatExport.lua
```

## License

MIT — see [LICENSE](LICENSE). Changelog: [CHANGELOG.md](CHANGELOG.md).
