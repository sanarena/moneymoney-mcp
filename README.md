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

## Install with AI (easiest)

If your AI can run commands on your Mac (Claude Code, the Code tab of the
Claude desktop app, Codex, Cursor, …), copy this prompt into it. It installs
the server, tests it against MoneyMoney, and adds it to your AI app with the
correct path. Chat-only websites (claude.ai, chatgpt.com) can't run commands,
so use the [manual install](#install-manually-2-minutes) there.

```text
Install the MoneyMoney MCP server (https://github.com/sanarena/moneymoney-mcp) on my Mac and connect it to you. It is read-only and needs no pip or uv. Run the commands yourself and only stop when I need to do something.

1. Check that /Applications/MoneyMoney.app exists. Run `xcode-select -p`; if it fails, run `xcode-select --install`, ask me to click "Install", and wait until I say it has finished.
2. Run `/usr/bin/python3 --version`; it must be 3.9 or newer.
3. If ~/moneymoney-mcp exists, run `git -C ~/moneymoney-mcp pull`. Otherwise run `git clone https://github.com/sanarena/moneymoney-mcp ~/moneymoney-mcp`.
4. Run `echo "$HOME/moneymoney-mcp/src/moneymoney_mcp/server.py"` and check that the file exists. That full path is SERVER_PATH below. Always write it out in full and in quotes: never ~, $HOME, or a placeholder like /absolute/path/to.
5. Tell me macOS may ask to let an app control MoneyMoney (I click OK). Then test:
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup","version":"1"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_status","arguments":{}}}' | /usr/bin/python3 "SERVER_PATH"
   It works when the output shows "moneymoney_running": true and "database_unlocked": true. If MoneyMoney is closed or locked, ask me to open and unlock it, then test again. If it timed out, test again. If it says "not authorized", ask me to allow it in System Settings → Privacy & Security → Automation.
6. Add a stdio MCP server named moneymoney, command /usr/bin/python3, one argument SERVER_PATH, to the AI app you are running in. Then ask me whether to also add it to my other AI apps.
   - Claude Code: run `claude mcp remove moneymoney -s local` and `claude mcp remove moneymoney -s user` (errors are fine), then `claude mcp add --scope user moneymoney -- /usr/bin/python3 "SERVER_PATH"`. `claude mcp get moneymoney` must show "✔ Connected" and the new path.
   - Codex: `codex mcp add moneymoney -- /usr/bin/python3 "SERVER_PATH"`
   - Claude desktop app: "mcpServers" in ~/Library/Application Support/Claude/claude_desktop_config.json
   - Cursor: "mcpServers" in ~/.cursor/mcp.json
   - Any other app: its own MCP settings file
   Before you edit a settings file, back it up. Keep everything else in it, replace any old "moneymoney" entry (an earlier attempt may have left a wrong path), and check the file still parses. Show me only the moneymoney entries, not whole settings files.
7. Tell me what you changed and how to restart: quit a desktop app completely (Cmd+Q) and reopen it, or start a new session in a terminal app. Then I ask "What are my account balances?" (macOS may ask once more to let that app control MoneyMoney).
```

## Install manually (2 minutes)

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
echo "$PWD/moneymoney-mcp/src/moneymoney_mcp/server.py"
```

then use command `/usr/bin/python3` with args `["<the path printed above>"]`,
e.g. `["/Users/anna/moneymoney-mcp/src/moneymoney_mcp/server.py"]`. It must be
the full path: `~` and placeholders don't work.

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
