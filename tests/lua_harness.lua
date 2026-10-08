-- Smoke test for AIChatExport.lua with a stubbed MoneyMoney environment.
-- Usage: lua tests/lua_harness.lua [path/to/AIChatExport.lua]
-- Fails (non-zero exit) on any assertion mismatch.
local path = arg and arg[1] or "extensions/AIChatExport.lua"

local out = {}
local orig_write = io.write
io.write = function(...)
  for _, v in ipairs({ ... }) do
    out[#out + 1] = tostring(v)
  end
  return true
end
function Exporter(t)
  assert(t.format == "AI Chat (Markdown)", "format name changed?")
  assert(t.fileExtension == "md")
end
MM = {
  localizeDate = function(fmt, ts)
    assert(fmt == "yyyy-MM-dd")
    return os.date("%Y-%m-%d", ts)
  end,
}

dofile(path)

local function render(withMM)
  if withMM then
    MM = { localizeDate = function(_, ts) return os.date("%Y-%m-%d", ts) end }
  else
    MM = nil
  end
  out = {}
  local account = {
    name = "Girokonto", accountNumber = "123456", bankCode = "TESTBANK",
    iban = "DE00123456", currency = "EUR", balance = 1234.5,
  }
  local txs = {
    {
      bookingDate = os.time({ year = 2026, month = 10, day = 2 }),
      name = "FreshMart", purpose = "Einkauf\nFiliale 5",
      category = "Food\\Groceries", amount = -42.5, currency = "EUR",
    },
    {
      bookingDate = os.time({ year = 2026, month = 10, day = 3 }),
      name = "Sats", purpose = "stacking", category = nil,
      amount = 0.00001, currency = "BTC",
    },
  }
  WriteHeader(account, os.time({ year = 2026, month = 10, day = 1 }),
    os.time({ year = 2026, month = 10, day = 7 }), 2)
  WriteTransactions(account, txs)
  WriteTail(account)
  -- Multi-currency table balance must not render as "table: 0x...".
  WriteHeader({ name = "Multi", balance = { { 586.42, "EUR" }, { 100, "USD" } } },
    os.time({ year = 2026, month = 10, day = 1 }),
    os.time({ year = 2026, month = 10, day = 7 }), 0)
  return table.concat(out)
end

for _, withMM in ipairs({ true, false }) do
  local text = render(withMM)
  assert(text:find("2026-10-02", 1, true), "date missing")
  assert(text:find("Food > Groceries", 1, true), "category separator not mapped")
  assert(text:find("Einkauf Filiale 5", 1, true), "purpose not flattened")
  assert(text:find("0.00001 BTC", 1, true), "crypto precision lost")
  assert(text:find("586.42 EUR, 100 USD", 1, true), "table balance broken")
  assert(not text:find("table: 0x"), "raw table leaked into output")
end

orig_write("LUA-HARNESS-OK\n")
