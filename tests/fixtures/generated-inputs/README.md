The universe fixture freezes only the symbol and name columns of every TW/US
row from generated `c4fd63c39e542149607eac7cea4a47f74840be99` (2026-10-23).
Theme membership/name validation and the full-market growth size test need
these columns, not changing daily report outputs. Retained financials, company
directories and `data/world/*.csv` continue to be read directly from main.
