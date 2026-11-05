# market-chart (Supabase Edge Function)

Read-only Yahoo Finance history proxy with a best-effort Taiwan MIS overlay for today. The browser cannot call Yahoo directly
(no CORS), so Atlas (`atlas/src/market-data/EdgeYahooProvider.ts`) calls this function, which returns
Yahoo's **raw** chart JSON. Atlas normalizes and validates it client-side. No secrets, no database.

```
GET /functions/v1/market-chart?symbol=2330.TW&interval=1d&range=5y&events=1
```

| Parameter | Rule |
| --- | --- |
| `symbol` | US ticker (`NVDA`, `BRK-B`), Taiwan `\d{4,6}[A-Z]?.(TW|TWO)`, Yahoo index `^XXX`, or `000001.SS` / `399001.SZ`. Upper-cased; anything else → 400. |
| `interval` / `range` | Whitelisted pairs (see `validate.ts`): `5m`/`15m`/`30m` → `1d`,`5d`,`1mo`; `60m` → `5d`,`1mo`,`3mo`; `1d` → `1mo`…`10y`; `1wk`/`1mo` → `1y`…`max`. |
| `events` | `1` (default) adds `events=div,splits`; `0` omits them. |

Batch live quotes (the site's once-a-minute refresh of the stocks on screen, `atlas/src/market-data/LiveQuotes.ts`):

```
GET /functions/v1/market-chart?quotes=AAPL,2330.TW,^GSPC
→ { "asOf": 1793722781, "results": { "AAPL": <chart JSON> , … }, "errors": { "XYZ": "not_found" } }
```

- Up to 40 symbols (same symbol rules as above; duplicates removed). Each is one Yahoo request
  `interval=1d&range=1mo` (no events), six at a time; the chart JSON keeps only `meta` (without
  `validRanges`/`tradingPeriods`), `timestamp` and `indicators.quote`.
- Each symbol is cached in the function instance for 20 s; the response has `Cache-Control: public, max-age=20`.
- After one `rate_limited` answer the rest of the batch is not requested (reported as `rate_limited`);
  the site then doubles its polling interval, up to five minutes.

Behaviour:

- Upstream: `https://query2.finance.yahoo.com/v8/finance/chart/<symbol>?interval=..&range=..&events=div%2Csplits&includePrePost=false`
  with a browser-like User-Agent and an 8 s timeout; on timeout/5xx/429 it retries `query1`.
- CORS: `Access-Control-Allow-Origin` is echoed only for `https://wudn9922.github.io` and
  `http://localhost:*` / `http://127.0.0.1:*`. Requests carrying any other `Origin` get 403. `OPTIONS` → 204.
  Optional env `ALLOWED_ORIGINS` (comma list) replaces the default origin list (localhost stays allowed).
- Caching: `Cache-Control: public, max-age=60` for intraday intervals, `max-age=600` for `1d`/`1wk`/`1mo`.
- Errors are sanitized JSON `{"error":{"code","message"}}` with `Cache-Control: no-store`:
  400 `invalid_symbol|invalid_interval|invalid_range|invalid_events`, 403 `origin_not_allowed`,
  404 `not_found`, 405 `method_not_allowed`, 502 `upstream_error`, 503 `rate_limited`.

`validate.ts` holds all pure logic and is unit-tested from Atlas
(`atlas/tests/market-chart-function.test.ts`, run by `npm test`).

## Deploy

```sh
supabase login                      # or export SUPABASE_ACCESS_TOKEN=<personal access token>
supabase functions deploy market-chart --no-verify-jwt --project-ref bxhqpfeberqbtxymghyt
```

`--no-verify-jwt` is required: Atlas calls the function without a Supabase key. Automatic deployment:
`.github/workflows/supabase-functions.yml` runs on pushes to `main` that touch this folder (or manually)
once the repository secret `SUPABASE_ACCESS_TOKEN` exists; without it the workflow only prints a notice.

Smoke test after deploying:

```sh
curl -i 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart?symbol=AAPL&interval=1d&range=1mo&events=0'
curl -i 'https://bxhqpfeberqbtxymghyt.supabase.co/functions/v1/market-chart?symbol=AAPL&interval=1d&range=99y'   # 400
```

Local run: `supabase functions serve market-chart --no-verify-jwt` (needs Docker).

## Limitations

Yahoo's chart endpoint is unofficial and may rate-limit or block cloud egress IPs (Supabase runs on
shared cloud infrastructure). Atlas then shows `暫時無法取得 <symbol> 行情，請稍後再試` and keeps any
cached data; nothing else on the site depends on this function.


## 台股盤中 MIS（維護者部署後生效）

只在台灣週一至週五 09:00–13:30，對 `.TW`、`.TWO`、`^TWII`、`^TWOII` 的 `interval=1d` 與 `?quotes=` 查詢 MIS。
對照為 `tse_2330.tw`、`otc_6488.tw`、`tse_t00.tw`、`otc_o00.tw`。股票尾碼字母支援 ETF；查詢網址使用固定主機與經驗證的代號。
既有 Yahoo 歷史／美股／分 K／週月原始請求、#224 的 5 分 K 補洞不變。MIS 與原 Yahoo 工作並行，完成後才覆蓋今天，不儲存或改寫任何永久 K 線。

- 同一批的台股只送一個 MIS 請求；adapter 每段最多 40 檔，超過分段。既有 `?quotes=` 上限仍為 40 檔。
- 同一 isolate 的請求串行，開始時間至少相隔 1 秒；成功與失敗都快取 15 秒，重疊查詢會重用快取。
  等候佇列超過 2.5 秒便退回 Yahoo，避免多人開頁時因節流而阻塞。跨 isolate 不共用此快取，也沒有新增背景排程。
- MIS 連線／讀取逾時為 2 秒；HTTP 失敗、JSON／欄位格式錯誤、代號／市場不符、非今日日期、非法／未來時間都不使用。
  休市日若沿用上次交易日資料，會被同日檢查排除；前端仍沿用既有交易日曆與 LiveQuoteFeed。
- `d+t` 以 Asia/Taipei 轉換 Unix 秒。今日 K 棒固定當日 01:00 UTC；同交易日的不同時間戳只留一列（含 #224 補入的首筆 5 分 K）。過去列不變。
- `z` 空白／`-` 不轉成 0。只沿用本 isolate **同交易日**最近有效成交價，並保留那次實際成交時間；跨日清空。
  沒有可沿用的成交價時，只更新既有今日列的開高低與量、保留 Yahoo close；沒有今日列就不建立，亦不使用昨收充當現價。
  若部分覆蓋會使原 Yahoo close 與高低價矛盾，保留原 Yahoo 列。
- 個股 `v` 張 ×1000 轉成股，前端仍用現有張數顯示；指數不推測 MIS 成交量單位，保留 Yahoo volume 或 null。
- Yahoo 無法提供資料時，有完整有效 MIS 成交價才回傳一根今日 K，標記 `atlasHistoryUnavailable=true`，不產生假歷史。
  例如櫃買指數：前端保留原 TPEx 歷史，透過同一批次 feed 接上今日；漲跌採 MIS `y`，不建立假的昨日 K。
- 有有效 MIS 成交價時，meta 為 `atlasLiveSource=twse-mis`，並更新 regularMarketPrice／regularMarketTime／chartPreviousClose。
  僅有開高低量時為 `atlasOhlcSource=twse-mis`，不標示為 MIS 即時成交價。
  前端保留歷史來源，另標「證交所即時（非官方 API）」；只有 Yahoo 時恢復原來源／延遲標示。
- Yahoo 快取不保存 MIS 覆蓋值，避免 MIS 失敗或收盤後把舊 MIS 值當成 Yahoo 回傳。
  盤中台股日 K 的 HTTP cache 為 15 秒，其餘原快取維持；`?quotes=` 仍為 20 秒，前端仍每分鐘批次更新，沒有新增輪詢。

### 驗證與交付

實作測試只用依使用者提供的 `msgArray` 形狀製作的假資料；不連線真實證交所、Yahoo 或正式 Supabase。

```sh
deno check supabase/functions/market-chart/index.ts
deno lint supabase/functions/market-chart
cd atlas
npm run lint
npm run typecheck
npx vitest run
npm run build
VITE_PUBLIC_BASE=/my-stock-screener/atlas/ npm run build:pages
npm run test:site -- live-quotes.spec.ts market-calendar.spec.ts pr-a-quote-status.spec.ts
npx playwright test --config e2e-site/playwright.compact.config.ts live-quotes.spec.ts pr-a-quote-status.spec.ts
```

測試在 Deno 2.5.6 驗證；沒有增加產品依賴。`tests/market-chart-handler.test.ts` 直接執行入口程式，所有上游都注入假 fetch；
`tests/twse-mis.test.ts` 涵蓋分批／節流／快取、跨日與空價、成交量、無量指數、失敗、非交易時段與來源還原。
瀏覽器涵蓋上市、上櫃、加權、櫃買，今日 K／來源及下一分鐘更新，並保留美股與既有盤中回歸。

發布由維護者依上方既有 Deploy 步驟執行：先部署 `market-chart`，再發布包含來源標示的前端。
兩端向後相容；不需 SQL、資料遷移、新環境變數或 API key。此 PR 不部署、不觸發正式 workflow，也不合併。
