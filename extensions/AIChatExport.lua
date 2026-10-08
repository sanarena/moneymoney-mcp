--
-- MoneyMoney AI Chat Export Extension
-- https://moneymoney.app/api/export/
--
-- Exports transactions as compact Markdown, optimized for pasting into an
-- AI chatbot (ChatGPT, Claude, ...). Install by copying this file into the
-- MoneyMoney Extensions folder (Help -> Show Database in Finder), then use
-- Account -> Export Transactions and pick "AI Chat (Markdown)".
--
-- Part of moneymoney-mcp (MIT License).
--

Exporter{
  version = 1.00,
  format = "AI Chat (Markdown)",
  fileExtension = "md",
  reverseOrder = true,
  description = "Export transactions as compact Markdown for AI chatbots"
}

local function text (value)
  -- Flatten a field for single-line Markdown output.
  if value == nil then
    return ""
  end
  value = tostring(value)
  value = string.gsub(value, "%s+", " ")
  value = string.gsub(value, "|", "/")
  return value
end

local function isoDate (timestamp)
  if timestamp == nil then
    return ""
  end
  if MM ~= nil and MM.localizeDate ~= nil then
    return MM.localizeDate("yyyy-MM-dd", timestamp)
  end
  return os.date("%Y-%m-%d", timestamp)
end

local function amount (value)
  if value == nil then
    return ""
  end
  if type(value) == "table" then
    -- Multi-currency balance such as {{586.42, "EUR"}, {100, "USD"}}.
    local parts = {}
    for _, pair in ipairs(value) do
      if type(pair) == "table" then
        parts[#parts+1] = amount(pair[1]) .. " " .. text(pair[2])
      else
        parts[#parts+1] = text(pair)
      end
    end
    if #parts == 0 then
      for k, v in pairs(value) do parts[#parts+1] = text(k) .. "=" .. text(v) end
    end
    return table.concat(parts, ", ")
  end
  local num = tonumber(value)
  if num == nil then
    return text(value)
  end
  local formatted = string.format("%.8f", num)
  formatted = string.gsub(formatted, "0+$", "")
  formatted = string.gsub(formatted, "%.$", "")
  return formatted
end

function WriteHeader (account, startDate, endDate, transactionCount)
  assert(io.write(
    "# Transactions: " .. text(account.name) .. "\n\n" ..
    "- Account: " .. text(account.accountNumber) ..
      " (" .. text(account.bankCode) .. ")\n" ..
    "- IBAN: " .. text(account.iban) .. "\n" ..
    "- Period: " .. isoDate(startDate) .. " to " .. isoDate(endDate) .. "\n" ..
    "- Count: " .. transactionCount .. "\n" ..
    "- Balance: " .. amount(account.balance) .. " " .. text(account.currency) .. "\n" ..
    "\n" ..
    "| Date | Name | Purpose | Category | Amount |\n" ..
    "|------|------|---------|----------|--------|\n"
  ))
end

function WriteTransactions (account, transactions)
  for _, transaction in ipairs(transactions) do
    local category = text(transaction.category)
    category = string.gsub(category, "\\", " > ")
    assert(io.write(
      "| " .. isoDate(transaction.bookingDate) ..
      " | " .. text(transaction.name) ..
      " | " .. text(transaction.purpose) ..
      " | " .. category ..
      " | " .. amount(transaction.amount) .. " " .. text(transaction.currency) ..
      " |\n"
    ))
  end
end

function WriteTail (account)
  -- Nothing to do.
end
