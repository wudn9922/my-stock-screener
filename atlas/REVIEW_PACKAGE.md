## 2026-11-10 PR review: one shared MA row per normalized ticker
Implementation: overwrite the grouped ID or reuse the dormant ID, convert group membership without deleting MAs, explicit parameter deletion, legacy ungrouped-first reads in Atlas/Python, and guarded cleanup/retry; 2408.TW/AGM/TSM were fixed separately by the maintainer, with no production SQL in this PR.
Verification: code/test updates submitted for Atlas typecheck/lint/Vitest/build/Pages, Playwright site desktop/mobile Chromium (WebKit CI), and Python monitor/report checks; actual CI pass/fail is reported in the PR, not inferred from historical records.

永豐唯讀同步：primary self-review，沒有獨立簽核；owner/IAM/共享密鑰、Decimal、CAS、跨 instance 限流及 API 唯讀邊界已檢查。
部署步驟見 sinopac_sync/README.md；所有 broker／Google／資料庫測試用假回應，沒有連線永豐、部署或執行 SQL。
相關 Python、TypeScript、Playwright、lint、build 的實際結果列於本次 PR；本 PR 接續尚未合併的 #250。
## 新增持股帳戶方式簡化（2026-11-08）

Primary self-review only；沒有獨立簽核。新增表單只分 API 自動同步與匯入 CSV。CSV 自填名稱、選台股／美股／加密貨幣，存 broker=other；MEXC API 沿用原連線與成本確認，存 broker=mexc。永豐台股即將推出且按鈕停用，永豐美股明示匯入 CSV。

既有帳戶的 broker／kind／currency 不變，未改 schema、後端驗證或正式資料。ImportHoldings 邏輯完全保留；測試改用新的建立入口，保留 mapping、交易紀錄重建、成本確認、權限與同步斷言。操作與390px截圖見 [HOLDINGS_ACCOUNT_METHODS.md](docs/HOLDINGS_ACCOUNT_METHODS.md)。實際測試計數列於 PR；只推 PR，不合併或部署。

顯示格式修訂：金額／盈虧兩位；價格≥1兩位、<1四位有效數字。原值保留於title與明細，計算／儲存／加總未變。
相關格式與持股測試、lint、build及假MEXC格式瀏覽器案例驗證；只有primary self-review，無獨立簽核。

## 持股精簡、刷新保留與分區總計（2026-11-08）

從最新main `36ba6a42a8` 開 `codex/holdings-compact-retained-values`。只有primary self-review，沒有獨立簽核；不合併、不部署、不操作正式帳戶。API、Supabase程式／SQL、帳號授權與資料格式不變。

可重現的BB缺價路徑：合法ticker批次暫缺單一代號時，原前端整份替換connection.quotes；revision短暫不同又會遮蔽原報價。現在以同帳戶RAM合併，刷新中／單次缺價保留原價、盈虧及原報價時間，新值才替換。新Key與斷線不帶入舊報價。一般公開報價和FX也保留。沒有取得正式事件的私人回應，不宣稱已證實BB現場每個時間點的根因。

股票／現貨一列一檔，全部成本、今日損益、來源、隱藏操作可展開；合約精簡四欄，更多保留張數、保證金與完整單位。資料先於操作，連線設定預設收合。美股區包含美股券商／加密現貨／合約equity，台股獨立TWD；equity只加一次，權益本金=equity-account.unrealized，不使用名目本金或重加逐筆PnL。USDT只有在原兩組TWD匯率都可用時換算，否則分列，日期註明。失敗合約保留在持倉區，但總計明示排除。缺價仍列已知成本，盈虧百分比未知。

驗證：npm ci、tsc、lint、build通過，完整Vitest164檔／2,156項通過。持股／MEXC／隱藏設定完整Playwright桌機與390px兩引擎93項、375px兩引擎62項全過。BB測試涵蓋進行中、合法成功卻缺報價、後續恢復；合約測試涵蓋刷新保留、失敗排除總計。新總計規則已取代原「合約不入總值」測試，仍精確驗證現貨小計、文件與時間不變。首輪舊提示文字斷言已修正後重跑全檔，沒有skip或提高timeout。

截圖皆為假資料與模擬裝置，位於 [screenshots/holdings-compact](docs/screenshots/holdings-compact/)。擷取時重設真正的捲動容器，檢查首筆不是負座標，並要求第五筆仍在手機可見範圍內；補拍時真實首屏斷言先抓到第五筆超出約13px，縮小卡片內距後同一斷言於五種環境10項全過，沒有放寬測試；共15張截圖。非實機iPhone驗證。部分上游價格本來就不是即時，沒有增加輪詢或報價來源。WorldAssetsView不是BB持股取價路徑，未改其行情行為。此次僅需前端發布，無新SQL或後端部署步驟。

## MEXC 合約高精度與自動唯讀載入（2026-11-08）

從 main `bb9bb3a112`（#245已合併）開 `codex/mexc-futures-refresh-precision`。只有primary self-review，沒有獨立簽核；只交付PR，不合併、不部署、不執行SQL、不觸發正式workflow、不發通知、不讀真實帳戶。

來源小數超過18位以BigInt捨入（半值遠離零），科學記號與正負PnL有回歸測試，槓桿仍嚴格為整數。無效的選填unRealizedPnl改用原fairPrice公式，計算成功不列入skippedPositions；診斷仍只有固定field及安全欄位名，沒有私人數值。畫面PnL最多8位。

新增holdings.mexc.futures，只讀合約，沿用雲端授權、AAD解密、唯讀權限檢查、最終撤權與換Key／中斷連線檢查。新手動SQL提供跨Edge實例的每帳戶30秒獨立限流及密文讀取／驗證RPC；只保存額度metadata，不寫現貨文件、syncedAt、snapshotId或holdingRevision，不占現貨同步額度。頁面可見時首次讀取、每60秒刷新；隱藏／離頁不發新請求，手動同步取代較舊請求結果。失敗保留上次成功值與資料時間，切換身分不帶入新帳號，全部只在RAM。

實測：npm ci、tsc、lint、Deno check／lint、production及Pages build通過；完整Vitest163檔／2,145項、MEXC專項5檔／160項全過。MEXC全檔Playwright桌機／390px Chromium／iPhone WebKit48項、375px兩引擎32項全過；最後補拍能看到時間與保留提示的畫面，重跑該案例5項全過。第一輪發現測試選舊導覽名稱、舊訊息及把合約GET算入現貨同步次數，已修正檢查並完整重跑；沒有skip、提高timeout或放寬現貨修訂／持股總計斷言。

SQL以pglast驗證9個statement與PL/pgSQL語法，並以fake relational handler測授權、獨立限流、跨owner、撤權、換Key與斷線競態；未執行SQL，因此不宣稱已驗證正式RLS或資料庫部署。維護者須先套用新增SQL，再重新部署user-settings，最後發布Atlas；無新環境變數、不更換主金鑰。

[完整說明與操作順序](../docs/ATLAS_MEXC_FUTURES.md#高精度來源與獨立合約讀取2026-11-08)、[測試與SHA證據](../docs/research-verification/mexc-futures-refresh-20261108.json)。[桌機](docs/screenshots/mexc-futures-refresh/desktop-auto-load.png)、[390px WebKit](docs/screenshots/mexc-futures-refresh/iphone390-auto-load.png)、[375px WebKit失敗保留](docs/screenshots/mexc-futures-refresh/iphone375-retained.png)，同資料夾另附兩種手機引擎；全為假資料與模擬裝置，非實機iPhone驗證。

## MEXC 合約缺漏欄位、精度與逐筆隔離（2026-11-08）

從最新main `b65eea0b46` 開 `codex/mexc-futures-position-resilience`。只有primary self-review，沒有獨立簽核；只開PR，不合併、不部署、不執行SQL、不讀取真實MEXC帳戶。

unRealizedPnl有值（含零）沿用，缺少／null／空字串才依fairPrice、優先全精度均價及張數×contractSize推算多空損益。金融運算全程BigInt，推算盈虧最後四捨五入8位，幣數18位，循環比值也可捨入；來源損益原精度保留。缺少、零或負強平價回null。單筆state／方向／模式／財務欄位不合法只略過計數，逐筆通過原契約後合併；全數略過有明確提示，整份容器或上游來源錯誤仍維持unavailable。

診斷增加固定field與最多40個安全positionKeys，未知鍵名遮蔽為[other]；單元測試掃描日誌確認沒有欄位值、幣名、金額、持倉ID或Key。現貨、skippedAssetNames、唯讀授權／加密／RPC、9秒期限、20檔逐代號、並行4、2 MiB及原有8 MiB全清單備援保持不變。

實測：tsc、Deno check／lint、完整Vitest162檔／2,112項（合約專項73項）、lint與production build通過。MEXC全檔Playwright桌機／390px Chromium／WebKit42項、375px兩引擎28項，合計70項全部通過。新增375px案例先取摘要再等到匯率更新，首輪2項失敗；補上既有案例相同的匯率就緒斷言後完整重跑通過，原整段總計相等斷言、10秒expect及60秒case timeout都保留，沒有新增skip。

[計算／診斷／部署說明](../docs/ATLAS_MEXC_FUTURES.md)、[測試與SHA證據](../docs/research-verification/mexc-futures-resilience-20261108.json)。[桌機](docs/screenshots/mexc-futures-resilience/desktop.png)、[390px Chromium](docs/screenshots/mexc-futures-resilience/chromium390.png)、[390px WebKit](docs/screenshots/mexc-futures-resilience/iphone390.png)、[375px Chromium](docs/screenshots/mexc-futures-resilience/chromium375.png)、[375px WebKit](docs/screenshots/mexc-futures-resilience/iphone375.png)全是假資料與模擬裝置，非實體iPhone／正式帳戶驗收。現行官方文件雖有unRealizedPnl欄位，本次兼容實際缺漏；沒有宣稱已讀到使用者真實回應。

## MEXC 合約逐代號讀取、跳過資產名稱與隱藏持股（2026-11-07）

只做 primary self-review，沒有獨立簽核；不合併、不部署。合併後須重新部署 Edge Function `user-settings`。

1. 合約：正式記錄 `futures-detail` `response-size-or-empty` 來自不帶參數的 `contract/detail/country` 全清單超過 2 MiB。改為只讀持有代號的 `?symbol=`（detail 與 ticker），同時最多 4 個；超過 20 個代號才回到兩個公開全清單並只對它們放寬為 8 MiB。固定 origin、GET、redirect error、9 秒共用期限、嚴格解析與不含代號的診斷不變；首個失敗即中止其餘請求並只記一筆。新增回歸測試：全清單超過 2 MiB 時舊實作為 unavailable、新實作為 ok 且不呼叫全清單。官方頁面因工作環境網路封鎖未能直接開啟，以搜尋索引的官方頁面確認參數，見 `docs/ATLAS_MEXC_FUTURES.md`。
2. 跳過資產名稱：connect／sync 回應新增 `skippedAssetNames`（原始名稱、最多 20、去重、去除控制／格式字元、40 字元），前端驗證後只放記憶體並顯示於原提示；伺服器 `asset-skipped` 記錄不變，不進 RPC、DB、status。原測試「回應不含名稱」依新需求改為「只在該回應、不在記錄／RPC／DB／status／URL／瀏覽器儲存」。
3. 隱藏持股：卡片「隱藏」、帳戶「隱藏設定」（小額門檻、USDT）、收合「已隱藏 N 檔」。總計、市場小計、帳戶小計與匯率提示只用可見列；持股文件不變。偏好經 `userStorage` 存 `holdings-visibility-v1`（cloud 也存在本機帳號空間，只含帳戶 id＋代號＋規則），限制見 `docs/ATLAS_HOLDINGS.md`。

實測：`npm ci`、完整 Vitest 161 檔／2,079 項、lint、`tsc --noEmit`、build 通過；Playwright `mexc.spec.ts`、`holdings.spec.ts`、新 `holdings-hide.spec.ts` 於 desktop-chromium 與 mobile-chromium 共 50 項通過（`--trace off`），375px 無水平捲動，截圖 `docs/screenshots/holdings-hide/mexc-hidden-375.png`。本機只有 Chromium，iPhone WebKit 交由 CI；皆為模擬，不是實體裝置驗證。

## 清單上一檔／下一檔與主要頁面保留狀態（2026-11-07）

只做 primary self-review，沒有獨立簽核。分支 `claude/list-nav-page-state` 從最新 main 建立，只推 PR、不合併或部署。

**清單脈絡**：選股結果（含連續圖表）、名單變化、自選行情與圖表、清單追蹤結果／新入選／出榜、突破訊號組別、題材與產業成分股、世界指數、原物料／匯率、債券 ETF、持股開啟個股時，傳入「目前顯示順序（篩選、排序後全部，不只虛擬化可見列）＋清單名稱」。脈絡只存在本次頁面記憶體（`src/app/listContext.ts`），history state 只放不透明鍵；URL 仍是 `page=chart&symbol=…`，重新整理或分享連結沒有脈絡、不推測。圖表（K線／價量／研究）與財務頁顯示「‹ 上一檔｜第 N／M 檔 · 名稱｜下一檔 ›」，保留目前週期與分頁，首尾停用；桌機 `[`／`]`（輸入框、textarea、contenteditable 內不作用）；手機只在清單列水平滑動，不碰圖表畫布。切換以 replace 更新紀錄，瀏覽器返回直接回清單。搜尋開啟沒有清單列。

**主要頁面保留**：自選、我的、市場三分頁、選股首次造訪後保持掛載（`src/app/KeptPage.tsx`）。隱藏頁渲染在脫離 document 的容器，因此沒有重複 id／標籤／測試選擇器，也不暴露給輔助技術；殼層不再重新渲染隱藏頁，依賴其他 store 的請求以 `useWhileActive` 凍結到返回時。即時報價訂閱、MEXC 計時與自動同步、突破訊號 focus 重新整理、對話框焦點陷阱與 Escape、頁尾說明 portal 都依 `usePageActive()` 停止；返回時依原有新鮮度規則（報價 20 秒重用、MEXC 5 分鐘）處理。內層捲動（選股清單等）與 `<main>` 捲動按頁記錄、同一檢視才還原；同頁換市場／群組／檢視仍回頂端。底部導覽重新開啟各頁最後路由（與原 `lastMarketSection` 合併為 `src/app/routeMemory.ts`；題材詳情仍不重開）。在目前頁再點一次：路由非預設時回到預設（清條件、預設群組或清單總覽，重新掛載該頁、回頂端、replace URL）；已是預設只回頂端。不刪任何自選清單、策略或雲端設定。

**測試修改**：`screener-filters.spec.ts` 原「temporary filters reset across groups, markets and fresh visits…」改名並改為新規則：群組／市場切換仍清條件、新造訪與重新整理以 URL 為準、經導覽離開再回來保留、再點選股重設；舊偏好仍不還原。新增 `e2e-site/list-navigation.spec.ts` 5 項（順序與首尾停用、週期／價量分頁保留、鍵盤或滑動、財務頁與搜尋、篩選＋捲動經返回與導覽保留後重設、自選清單保留後回總覽、隱藏期間以 Playwright clock 快轉逾 4 分鐘並觸發 focus／visibility，無資料請求與輪詢），單元測試 `tests/list-navigation.test.ts` 7 項。

**實測**：npm ci；完整 Vitest 161 檔／2,044 項、lint、typecheck、production build 通過。整套 e2e-site 於桌機＋手機 Chromium 754 項：745 通過、4 skipped、5 失敗；失敗為 `site.spec.ts` 兩項（預設首頁為自選而非大盤，兩專案共 4 項）與手機 `workspace-refinements` 標頭高度 124.30 > 124，三者在未修改的 main 原始碼上以相同方式失敗，與本次無關、未修改。rebase 到最新 main 後重跑相關 172 項（新增 spec、選股全部、控制台、自選、圖表、導覽、休市）全部通過。

**限制**：清單脈絡是開啟當下的快照，之後清單內容變動不回寫；切到不在清單內的股票時隱藏清單列。隱藏頁若遇帳號切換等全站事件，只在返回時重新讀取。iPhone WebKit 本機未安裝，交由 CI；Playwright 為模擬裝置，非實機。[手機清單列](docs/screenshots/list-nav/chart-list-bar-375-mobile-chromium.png)。

## PR #240 修訂：根目錄匯入與保守三日比較（2026-11-07）

只更新原PR，沒有合併PR、dispatch、部署或寫正式archive。一般merge main `6e279a94bf`（#241），merge commit `aaeb4e0318`；REVIEW_PACKAGE前置段落衝突保留雙方紀錄，沒有rebase／force-push。只有primary self-review，沒有獨立簽核。

先重現根目錄`import report_metrics`的ModuleNotFoundError，再修正market_sessions的package／script匯入。repair_kline_archive依`__package__`選相對或直接匯入，維持模組實例一致。5項獨立程序回歸移除PYTHONPATH，涵蓋report_metrics、scripts.market_sessions、scripts目錄匯入、修補工具package匯入及兩種CLI --help，避免整批測試的sys.path改動掩蓋每日報告問題。

仍只從最近前後各兩個官方交易日取證；三日須不同日期、前後各至少一日，所有archive/official比值都在1±0.5%內才准factor=1。factor≠1維持完整四日／前後各兩日與原穩定度、中位數、反向調量；不足或不穩定仍跳過。合成archive測試核對三日稽核、官方價量原值及既有列不變。

完整Python **1,991通過、182子測試通過**；原有選跑7,000檔效能案例另行啟用也通過，合計1,992個案例已驗證，未新增skip。apply161.241秒、verify94.237秒，原300秒限制不變。CI同方式單獨執行test_report_metrics.py的41項也通過；匯入／補檔／報告專項71項及19子測試通過。前端完整單元、lint、build與actionlint實際結果見[修訂驗證總表](../docs/research-verification/official-repair-revision-verification-20261107.json)，不沿用初版計數冒稱本次結果。

固定archive `62bd86a6e7fcde3572c56aa607eed5018a0cfe68`，新舊規則皆以apply=False／offline=True共用32份來源，各跑前後全量verifier；41,573年檔、9,796,080列的內容、tree及來源快取均不變。2021-04-06新增1,727→1,743、跳過495→479；2025-08-01新增1,783→1,823、跳過247→207。原3,510筆候選逐列不變，新增56筆皆三日原值，調基仍661／216檔。初版與此次重取來源僅2021-04-06的1,019筆TWSE來源URL改用已核准官方等價路徑，價量／factor／比較值與跳過名單相同；本次新舊比較使用同一快取。

[完整前後對照與新增56筆佐證](../docs/research-verification/official-backfill-three-comparisons-20261107.json)、[操作與限制](../docs/ARCHIVE_OFFICIAL_REPAIR.md)。初版3,510列寫入／刪除／還原演練及25項瀏覽器紀錄仍保留為歷史，本次沒有將新計畫寫入任何正式封存。

## PR #240 初版紀錄：官方封存修補、完整還原及休市佐證（2026-11-07）

一般 merge 已合併 #230 的 main `0f42fef0b`，merge commit `cdd648d7f`；沒有另納入 #230 分支、rebase 或 force-push。只有 primary self-review，沒有獨立簽核。只推程式與證據 PR，沒有正式 archive 寫入、dispatch、合併或部署。

官方 MI_INDEX／TPEx dailyQuotes／MI_5MINS_HIST 依日期與欄位解析，四個最近官方交易日的 archive/official 比值檢查後才補缺列；不覆寫既有列。零量／缺價統一跳過，指數無量保留未知。CSV／index／manifest／backfilled audit 沿既有 writer，factor 與比較入稽核。TW 10/23 指定零量平盤用固定2214±1%保護；先 staged驗證、完整備份/fsync，再搬入。恢復先驗Git原件blob及整tree；原歷史驗證只增加全部內容精確回到指定刪除Git parent的窄例外，美股／其他日期不能借新reason刪除。

四個官方空行情日加入嚴格雙來源seed並從缺漏名單移除；交易證據否決、未知量保護保留。10/23刪列後仍有已查核證據，前端投影cache v6、raw cache不改。日週月聚合與實體刪列前後資料序列相同。新增workflow預設dry-run/read權限；備份artifact成功後，apply=true才可用獨立write job精確tip驗證再非force推送。#236跨event取代修正與新producer故障／取消備援均覆蓋。

實測：完整Python1,982 passed＋168 subtests；修補專項21項、獨立壓力案例1項通過（apply161.493秒、verify92.126秒，原300秒限制）。完整Vitest159檔／2,029項、lint、build、actionlint通過；一輪並行重負載的SEC舊案例5秒timeout，等重負載結束後以單worker完整通過，未改timeout／斷言或新增skip。桌機／390px Chromium／iPhone WebKit15項及375px兩引擎10項，共25項瀏覽器通過；為模擬裝置。21次取消按當時存在run回放0通知。

固定完整快照41,573年檔／9,796,080列，唯讀dry-run前後verifier通過。無remote完整副本新增3,510列、刪2,214列、還原2,214列；4,430個檔案與刪前內容tree逐位元組一致，Git tree均為 `f1a5b15680b895346066bf0d4f2e2577da574b9d`。原快照保持乾淨。[操作與端點](../docs/ARCHIVE_OFFICIAL_REPAIR.md)、[驗證總表](../docs/research-verification/official-archive-repair-verification-20261107.json)、[全量演練](../docs/research-verification/official-repair-rehearsal-summary-20261107.json)。原始演練恢復檔另打包留在工作區，不能當作正式dispatch的恢復artifact。

## 選股：本益比河流分位篩選（2026-11-07）

只做 primary self-review，沒有獨立簽核。新增篩選條件 `per:min:max`：目前本益比在該股自己歷史本益比中的百分位（0–100，含上下限），預設選項對應河流圖 P10／P30／P70／P90。演算法沿用個股頁 `buildPeRiver`（同一 EPS TTM／年度備援、發布延遲、PE>200 與虧損排除、百分位定義），不另寫一套。

全市場分位由 `scripts/build-pe-river.ts` 在 Atlas 每日建置時預先計算：讀 `docs/report/financials/*.json` 與永久封存（`archivePeriod` 週線，與瀏覽器同一休市日排除），輸出 `valuation/pe-river-{tw,us}.json`。封存只有 2021 年起，個股頁另併 Yahoo 較早週線，因此兩者分位可能略有差異，篩選說明與檔案 `basis` 都明示。封存 checkout 或計算失敗時保留上次部署的檔案，兩個步驟都有 id 並列入部分失敗通知。

沒有河流（歷史不足、外國發行人、幣別不符）或沒有目前本益比（虧損、EPS 趨近零、無近期 EPS）的股票一律不入選；檔案載入中或無法取得時顯示明確狀態，不顯示未篩選結果，套用鍵在範圍無效或資料不可用時停用。已存自選清單（瀏覽器與每日 collector）同樣讀取此檔，缺檔時沿用既有「保留上次清單」行為。

實測：完整 Vitest 161 檔全部通過（新增 9 項單元測試）、lint、typecheck、build 通過；桌機／手機 Chromium 30 項瀏覽器回歸（含新 3 項）通過，本機未安裝 WebKit，交由 CI。以真實財報與封存抽樣（2330.TW、2317.TW、1101.TW、AAPL、NVDA）驗證腳本輸出；全市場執行時間待 CI 實測。

## PR #230 合入 #236 後的部分失敗通知整合（2026-11-07）

一般 merge 最新 main `63f4236aa`，merge commit `788be46ff` 保留兩個 parent；REVIEW_PACKAGE 的文件衝突保留雙方紀錄，沒有 rebase 或 force-push。只做 primary self-review，沒有獨立簽核。

發布衍生休市證據步驟新增 `id: archive_closure_publish`，並將原步驟名稱加入 `collect_notification_outcomes` 的 `NOTIFY_STEP_NAMES`。原 `continue-on-error`、執行條件與 job 結果不變。回歸測試補強所有 producer 的 soft-failure ID 必須納入映射，並實際驗證發布步驟 outcome=failure／conclusion=success 時能輸出正確部分失敗摘要。

先重現原 `test_all_producers_notify_without_altering_review_workflows` 失敗，再驗證指定三檔 45 項全部通過（failure notifications 13、cancelled policy 12、market closures 20）；全部 workflows 的 actionlint 1.7.12 通過。完整 Vitest 159 檔／2,025 項、lint、build、diff check 通過。未放寬斷言或新增 skip／timeout。只推回原 PR，不合併 PR、不部署、不 dispatch 正式 workflow。

## PR #230 再修訂：交易日資料缺漏保護（2026-11-07）

本節取代下方前次的2021-04-06休市判定。只做primary self-review，沒有獨立簽核。一般merge最新main，保留已合併#235與原導頁／匯入測試修正；不rebase／force-push。

XTAI／XNYS先判日曆日。交易日須「主要指數缺列＋≥95%有效普通股零量平盤」才判臨時休市，且官方當日資料可否決；正量≥5%／50檔與原覆蓋門檻不變。2021-04-06官方^TWOII保留，改列missing-session-data；10/23、10/26結果不變。舊index-only政策撤銷，前端嚴格驗日曆／雙證據，完整行情投影cache升v5；raw年檔不動。

唯讀掃描固定archive `62bd86a6e7fcde3572c56aa607eed5018a0cfe68`，7,689代號、41,507年檔、9,780,006列、0無效OHLC，103.851秒；只有10/23含2,214筆疑似休市占位列。新[缺漏報告](../docs/research-verification/market-data-gaps-20261106.json)列6個TW歷史疑點，其中4/6與2025-08-01有TPEx佐證；另外4日待核對臨時休市／日曆差異。US既有範圍內0日，跨日後最新11/6尚未封存另列尾端。各日普通股缺列檔數與限制見[方法及摘要](../docs/MARKET_CLOSURES.md)、[ADR-029](docs/architecture/ADR-029-market-closed-session-projection.md)。掃描前後archive乾淨，無修復／推送／刪除。

本機相關Python套件336項通過（最後休市專項20項重驗），breadth pytest27項通過；完整Vitest157檔／1,973項、lint、typecheck、一般與Pages建置通過。受影響桌機／390px Chromium／WebKit 63項、375px兩引擎18項，共81項瀏覽器通過。沒有新增skip、放寬斷言或增加timeout。日曆套件移到universe-full掃描前安裝，archive review也安裝相同既有版本範圍；新的掃描ID可由#236收集部分失敗。全部workflow actionlint通過。

[4/6桌機](docs/screenshots/market-closures/calendar-gap-desktop.png)、[390px Chromium](docs/screenshots/market-closures/calendar-gap-chromium390.png)、[390px WebKit](docs/screenshots/market-closures/calendar-gap-iphone390.png)、[375px Chromium](docs/screenshots/market-closures/calendar-gap-chromium375.png)、[375px WebKit](docs/screenshots/market-closures/calendar-gap-iphone375.png)為離線fixture，不是實體iPhone或正式即時行情驗收。只更新原PR，不合併、不部署、不觸發正式workflow、不通知；永久行情／財報、突破快照與前瞻CSV未改。

## PR #230 前次修訂紀錄：股票成交證據優先（2026-11-06）

只做primary self-review，未聲稱獨立簽核。依使用者修正原prompt的OR規格：有效普通股正量占比≥5%或至少50檔時一律保留當天資料；指數缺列只記 `primary-index-gap-with-trading`。沒有任何股票觀測時仍可依主要指數缺列判休市，95%零量平盤門檻不變；成交證據優先處理5%／95%恰好相交的邊界。

以一般merge納入main `2e8799ba4d`（#231），交付前再合入最新main `e2ea0322c5`（#229／#232／#233），保留來源備援與取消生命週期，文件衝突保留雙方內容，沒有rebase／遠端force-push。移除bootstrap／ETF補充中繞過股票證據的單獨指數缺日判定；它們只採完整掃描的市場policy。stage與每日快照重新檢查成交，撤銷舊誤判；較新的人工查核證據不因下次快照無該日股票而被零數值覆蓋。前端嚴格驗證正量數，保留11/04，完整行情cache換版。

最新唯讀掃描archive `339f57e742d904976eefbf73b7fad506fff6d9c7`：7,689代號、41,507年檔、9,772,433列、0無效OHLC，59.225秒。普通股／ETF仍只有TW2026-10-23的2,214筆；但新回補另有^TWOII 2021-04-06一筆，當天普通股0、^TWII缺列，依指定的無觀測規則列出人工核對。因此最新完整報告是2個日期／2,215列，未把它隱藏成只有10/23。[JSON報告](../docs/research-verification/market-closure-scan-20261106.json)、[方法與限制](../docs/MARKET_CLOSURES.md)、[ADR-029](docs/architecture/ADR-029-market-closed-session-projection.md)。archive前後git status乾淨，無任何publish／修復／刪除。

實測：原相關Python套件273項通過；最後追加的成交證據保留案例以14項休市專項重驗。完整Vitest154檔／1,931項通過，lint／typecheck／一般與Pages建置通過。受影響桌機／390px Chromium／iPhone WebKit 51項、375px兩引擎16項，合計67項瀏覽器通過。未新增skip、增加timeout或放寬原數值斷言；原SEC測試在首輪高負載超時，減少並行後以原設定完整重跑通過。

合入最新main `e2ea0322c5` 後再次驗證：122項Python（休市、警示、交易時段、指數讀取器、封存）與155檔／1,942項Vitest全部通過；lint、typecheck、一般／Pages建置通過；11/04日週月保留案例在桌機、390px Chromium／WebKit、375px Chromium／WebKit共5項通過。新main的archive取消測試為每個實際請求建立獨立Response，補入休市policy請求，保留單一消費者取消不影響其他消費者的原斷言。

GitHub首輪完整瀏覽器935通過但2項舊案例失敗；實際下載artifact trace確認導頁成功旗標被另一個index相依檔覆寫，另有頁尾先於懶載入控制台出現的點擊競態。只修測試觀測與頁面就緒條件，新增新版WorldPage請求斷言，所有原斷言／timeout保留；沒有改登入、帳號或產品程式。[完整trace說明](docs/CI_FOLLOWUP_230_235.md)。修訂後兩檔27項桌機／390px Chromium／WebKit，以及14項375px兩引擎全部通過；lint／typecheck再次通過，完整CI對最後head重跑。

新增案例涵蓋11/04指數缺列但100檔股票成交時不休市、stage正常帶出11/04、scanner更正舊policy、前端日週月不排除11/04；10/23全部零量、10/26完全無股票觀測仍判休市。5%與50檔門檻均測，舊成交證據的數值在次日保留。

[桌機11/04](docs/screenshots/market-closures/index-gap-desktop.png)、[390px Chromium](docs/screenshots/market-closures/index-gap-chromium390.png)、[390px WebKit](docs/screenshots/market-closures/index-gap-iphone390.png)、[375px Chromium](docs/screenshots/market-closures/index-gap-chromium375.png)、[375px WebKit](docs/screenshots/market-closures/index-gap-iphone375.png) 是合成行情版面／截止日證明，不是實體iPhone或正式行情驗收。未改登入、Supabase、GCP、永久資料、突破舊快照或前瞻log；不合併、不部署、不執行正式workflow。

## MEXC 唯讀合約部位（2026-11-07）

Primary self-review only，沒有獨立簽核。從 main `41f87eaae` 建立，只推 PR、不合併或部署。依任務「官方文件為準」採現行 Futures V1 固定網域 `api.mexc.com`、`detail/country` 與 ticker.fairPrice；與預期的舊網域／detail 路徑差異、官方連結及欄位映射見 [合約 API 與資料隔離說明](../docs/ATLAS_MEXC_FUTURES.md)。網域固定，不接受任意 URL。

現貨來源成功驗證後，才在原有 9 秒 controller 內讀取合約；最終授權與 revision／finish 檢查仍在全部上游 I/O 之後。合約來源錯誤一律成為該區塊 no-permission／unavailable，現貨照常完成。缺 CONTRACT_ACCOUNT_READ 時不發合約請求。僅固定 GET、2 MiB、redirect error；金融數字以原 JSON token 保留精度，張數換幣數使用精確 BigInt，負數只允許未實現盈虧。獨立回應驗證器與帳戶 RAM 狀態不進 RPC、SQL、holdings document、持股總值或資產配置。

診斷只記允許的 step／HTTP status／固定 reason／有效 code／白名單訊息。測試涵蓋實際 handler 的簽名與 headers、權限／HTTP／success:false／無回應 transport 的9秒期限、精度、部位限制、欄位與診斷隱私，以及手動／自動同步、狀態文字、總計不變與手機無溢出。首輪新增測試修正了可見文字對 textContent 的比較差異，及匯率晚到造成的基準就緒問題；沒有放寬金額或溢出斷言、沒有增加 timeout 或 skip。

驗證通過：npm ci；完整 Vitest 158 檔／2,017 項，合約與 RAM 專項49項，完整 MEXC 三引擎39項，375px Chromium／WebKit新增合約4項，lint、production build。

沒有使用真實 Key、私人 MEXC 帳戶或正式資料庫；只對官方公開端點做相容性與大小檢查。Playwright 為模擬 iPhone。畫面：[桌面](docs/screenshots/mexc-futures/desktop.png)、[375px WebKit](docs/screenshots/mexc-futures/iphone375.png)。

## MEXC 特殊資產名稱跳過與計數（2026-11-07）

Primary self-review only，沒有獨立簽核。分支從 main `799fa85d2` 建立，只推 PR、不合併或部署。MEXC 非零餘額名稱先轉大寫，再套用原本的 ASCII 英數 1–20 字元規則；不合法或正規化後重複的項目跳過並計數，重複保留第一筆、不加總，零餘額不計。全部跳過仍回成功空清單。金額格式、來源大小、持股上限、唯讀權限、加密與授權檢查不變。

`connect`／`sync` 成功只額外回 `skippedAssets` 數字，沒有送入 RPC 或資料庫。前端於個別帳戶的 RAM 顯示指定提示，涵蓋手動與自動同步，正常後續同步或中斷連線清除。`validateMexcState`、SQL 及全站持股代號規則保持原樣。安全摘要每次解析一行，固定 `reason: asset-skipped`，`reasons` 分開統計 `asset-invalid`／`asset-duplicate`，僅附固定分類；測試確認無資產名稱、餘額或 Key。行為與範例見 [MEXC 說明](../docs/ATLAS_MEXC_SYNC.md)。

驗證：乾淨 npm ci，Vitest 156 檔／1,977 項，MEXC 專項 75 項，桌面 Chromium／手機 Chromium／iPhone WebKit 共33項，lint 及 production build 通過。沒有呼叫真實 MEXC 帳戶或正式資料庫，沒有修改 SQL 或進行部署。WebKit 為 Playwright 模擬。

## MEXC 同步診斷、回應與期限提醒（2026-11-06）

Primary self-review only；沒有獨立模型簽核。從最新main `2e8799ba4d` 建立 `codex/mexc-sync-diagnostics`，與PR #230無相依；交付前一般merge納入最新main `e2ea0322c5`，保留#229／#232／#233，僅REVIEW_PACKAGE前置段落衝突，兩邊內容皆保留。只推PR，不部署、不執行SQL、不查正式資料庫、不呼叫真實MEXC帳戶。既有授權、AES-GCM／HMAC、private表／RPC、持股成本與同步頻率不變。

[操作、官方依據與維護者部署順序](../docs/ATLAS_MEXC_SYNC.md#2026-11-06-同步診斷與金鑰期限修訂)、[ADR-020](docs/architecture/ADR-020-mexc-read-only-holdings.md)。三個官方端點目前範例為裸物件／ticker陣列；apikey／accessKey兩別名保留。code:0 envelope是兼容修正，尚未觀察到正式帳戶回應，不能宣稱它就是根因。官方有寫入權限的範例仍須拒絕；free＋locked、不重複available、18位精度與上限仍嚴格。

失敗診斷只有step／status／topLevelKeys／固定reason，4xx或含code時才可有數字code／安全msg。未知鍵名與非白名單msg遮蔽；連parser失敗也使用實際HTTP外層鍵名。不得記錄URL／query／簽章、原始例外、Key／Secret、資產／數量／餘額或owner。單元測試逐一掃描全部warning及allowed-key集合，含上游惡意回顯與未知內部回應。成功不產生日誌。

失效訊息依帳戶留在RAM，重讀狀態也保留；不清空上次持股、成功時間或密文。成功更換／同步／斷線才清除該帳戶錯誤，其他帳戶不受影響。connectedAt滿80日提醒未綁IP約90日期限，是提示而非精確到期日。

實測：npm ci、lint、typecheck、Deno check／lint、一般與Pages建置通過；完整Vitest155檔／1,954項通過。桌機／390px Chromium／iPhone WebKit的mexc＋holdings共57項通過；375px兩引擎的mexc共18項通過，總75項，無新增skip、放寬斷言或增加timeout。前端只新增本次提醒／帳戶錯誤狀態，沒有修改登入或設定API傳輸。

[失效：桌機](docs/screenshots/mexc-diagnostics/mexc-key-expired-desktop.png)、[375px Chromium](docs/screenshots/mexc-diagnostics/mexc-key-expired-chromium375.png)、[375px WebKit](docs/screenshots/mexc-diagnostics/mexc-key-expired-iphone375.png)；[期限提醒：桌機](docs/screenshots/mexc-diagnostics/mexc-key-age-desktop.png)、[375px WebKit](docs/screenshots/mexc-diagnostics/mexc-key-age-iphone375.png)。同資料夾另附390px兩引擎與其他375px截圖。全部為假帳戶／假持股／假Key，不是實體iPhone或正式連線認證。

合入最新main後再驗：完整Vitest156檔／1,965項、typecheck、一般與Pages建置通過；基本連線與80天／失效案例在桌機、390px與375px兩引擎另10項重驗通過。

GitHub首輪完整瀏覽器930通過，但導頁復原舊案例的成功旗標被另一個index相依檔覆寫。對照#230實際artifact trace，只修共用測試的觀測與頁面就緒條件，新增新版WorldPage請求斷言，並先等懶載入控制台完成再操作頁尾；沒有修改登入、帳號或產品程式。[證據與方法](docs/CI_FOLLOWUP_230_235.md)。原兩檔27項桌機／390px Chromium／WebKit與14項375px兩引擎全部通過，lint／typecheck再次通過；未增加timeout／skip，完整CI對最後head重跑。

本修訂不需新SQL或更換主金鑰。維護者先更新user-settings，再發布Atlas並刷新舊頁；本次皆未執行。正式根因需由維護者部署後依安全診斷核對，不請使用者提供金鑰或原始帳戶回應。

## 指數下載重試、唯讀封存備援與市場隔離（2026-11-05）

Primary self-review only；沒有獨立模型或 Claude 簽核。分支 `codex/index-daily-resilience` 從 main `c3f5c76511` 建立，先以一般 merge 納入 #228，待其合併後再以一般 merge 合入最新 main `afe469f522`（包含 #228 的 `21d49127ee`）。沒有 rebase／force-push；只交付 PR，不合併、不部署、不觸發正式 workflow。

共用 `scripts/index_daily.py` 先批次 Yahoo 下載，只對失敗成員逐檔最多重試2次（5／10秒）。明確 RateLimited／YFRateLimitError／429 使用既有30／60秒退避，持續限流後停止追加 Yahoo 請求。之後唯讀使用已 checkout archive 的 per-symbol index 與年度 CSV，驗 SHA-256、bytes、rows、日期與 canonical 格式。只用 XTAI／XNYS 已收盤 session，價格不作股利還原，不混接來源，不降低原本60／400根及 breadth 同日要求。來源寫入各指數 meta；SOX／SOXX 的價量來源獨立記錄。

警示單市場指數失敗時，另一市場正常更新；相同 params 的失敗市場保留原數值、資料日及計算時間，另標 retained／checkedAt／reason。首次沒有舊內容則 unavailable，沒有替代數值。兩市場指數都失敗時，整份舊 JSON／CSV bytes 不動。retained／unavailable 不追加前瞻紀錄；既有 market-warnings-log.csv 已比對最新 main 完全一致。名單／覆蓋失敗的原有整份保留行為不變。嚴格 Zod 保留舊文件相容性，新增狀態／來源驗證，UI 明示未更新及原因。

本機驗證：npm ci、lint、typecheck、一般 build、Pages build 全通過；Vitest 153檔／1,918項；Python 共用讀取器10項、market warnings25項（含#228）、breadth signals pytest27項、generated真實Git整合35項。桌機／390px Chromium／iPhone WebKit 33項，375px Chromium／WebKit 10項全部通過；最後畫面取景6＋4項亦通過。actionlint 1.7.7 通過。未新增 skip 或放寬斷言／timeout。最終 GitHub CI 結果以 PR checks 為準。

全部來源故障測試離線，沒有向真實 Yahoo 發請求，也沒有重跑正式排程；封存 fixture 由實際 writer 的格式產生，並驗證讀取前後 bytes 相同。實際備援能力仍依已完成封存的日期／根數，資料太舊仍會失敗。封存四位小數與 Yahoo 浮點資料可能略有精度差異，不改研究公式或門檻。更正先前將試算320根誤寫為正式快取上限的說明，未截短快取或改計算。

截圖為合成 fixture 的狀態／來源版面，不是真實行情；以1100px高視窗展示內容，手機寬度維持390／375。已目視檢查 [桌機](docs/screenshots/index-daily-resilience/desktop-retained.png)、[390px WebKit](docs/screenshots/index-daily-resilience/iphone390-retained.png)、[375px WebKit](docs/screenshots/index-daily-resilience/iphone375-retained.png)；同目錄另有 unavailable 截圖。這是 Playwright 模擬，不是實體 iPhone 驗證。沒有修改登入／Supabase／SQL／GCP／永久價格財報／archive 分支，沒有新增通知。

## WebKit 舊版匯入頁面就緒（2026-11-06）

Primary self-review only；沒有獨立簽核。`legacy-import.spec.ts` 的卡片在 ConsolePage 的 Suspense 外先出現；原 CI 在整頁尚未掛載時點擊，版面替換使輸入未落在匯入按鈕。只有新增既有 region「原有均線參數控制台」可見的前置斷言，五處入口共用；原全部步驟／click／斷言逐字保留，10秒 expect／60秒 case timeout 不變。

原始 CI trace／截圖、受控 native pointerdown→LABEL pointerup→MAIN click 的重現，以及修正後同一交錯的成功，見 [LEGACY_IMPORT_WEBKIT.md](docs/LEGACY_IMPORT_WEBKIT.md)。正常前後各20次皆通過，沒有宣稱本機量到失敗率改善；診斷原版1失敗／修正版1通過，兩組證據分開記錄。可重跑腳本只用 fixture 與本機 ConsolePage 回應，不改正常測試輸入。

本機63個帳號隔離／匯入／控制台、14個375px匯入回歸通過。同步main後完整154檔／1,935單元、lint、一般與Pages建置通過。沒有改正式程式、帳號授權、Supabase／SQL、workflow或正式資料。#232已由遠端合併，此附加工作另推PR，不合併或部署。

## WebKit 研究頁面導頁回退（2026-11-06）

Primary self-review only；沒有獨立簽核。從 main `afe469f5229fd66b3515cfa473053dd81480a229` 建立 `codex/webkit-research-smoke`，只推 PR、不合併或部署。

根因為 WebKit 在 `pagehide` 前取消 archive，TypeError 被轉成缺檔回退，導致停止中的文件啟動新 manifest fetch。獨立 Node HTTP + 真實 provider 可重現，排除 Python 預覽回應／Range；完整時序與前後各20次逐次證據見 [RESEARCH_PAGES_WEBKIT.md](docs/RESEARCH_PAGES_WEBKIT.md)。

審查重點：共用下載的 owner 是文件，單一圖表不能取消其他訂閱；listener 覆蓋本文且在 finally 移除；取消不可被年度 allSettled 的部分成功掩蓋；只有取消的 index 拒絕會清除以供重試。beforeunload 不阻止導頁、不新增提示，取消離頁後可重讀，BFCache 舊測試保留。真正的404／503／網路／逾時仍回退。

原 smoke 只加輸出路徑選項，所有旅程、pageerror 收集與斷言完全保留；沒有改既有 unit/e2e spec、伺服器、workflow、Supabase 或正式資料。新真實 HTTP 回歸透過既有 test:research-pages 指令接入 CI。確切檢查計數列於 PR 與 proof；不是實體 iPhone Safari 簽核。

## 市場多頭末期警示與波段計數（2026-11-05）

Primary self-review only；沒有獨立模型或 Claude 簽核。分支從 main `e28a5bed4cb87468f7ae7c71bf7a0ca4a88d0d64` 建立；一個 PR，不合併、不部署、不觸發正式排程。
PR建立期間main已合併#225並更新記錄；以一般merge合入`11d216c89c`，只解REVIEW_PACKAGE的雙方前置段落衝突，兩份內容皆保留，沒有rebase或force-push。相對最新main仍無Supabase／永久資料差異。

新卡片、計算、來源與限制見 [MARKET_WARNINGS.md](docs/MARKET_WARNINGS.md)、[ADR-028](docs/architecture/ADR-028-market-warnings.md)。研究原文保存在 `scripts/market_warnings_evidence.json`，卡片直接讀輸出原文。

審查重點：`swing_cohort.py` 只改首次／更高／合格收盤的計數，沒有更動 ATR 波段偵測、箱型或 EMA。`build_market_warnings.py` 排除未收盤 bar，指數兩個方向各有分母，756日門檻先 shift(1)。領頭羊為每個觀察日 t−1 的名單，窗口 t−(W+4)…t−5，最後五日平均。已知零量與未知量分開；缺少歷史不猜成正常。

CSV 以 (market,asOf) 首次紀錄為準，保存在 main durable records。兩種發布模式都驗證前綴，拒絕改寫、截短、刪除；並行新增做非 force 聯集，同鍵保留先發布者。既有快照一律不動，舊格式明示舊口徑，混合版本不畫比較柱。

本機實測：npm ci、lint、typecheck、一般與 Pages 建置通過；151檔／1,870個 Vitest。完整 Python 執行1,849項、0失敗，1個原有需另啟用的7,000檔封存效能基準未啟用；最後新增的觀察日名單案例另以18個警示專項完整通過。generated真實Git整合35項、breadth pytest24項通過。117個受影響桌機／390px Chromium／iPhone WebKit回歸、45個警示／突破、30個375px兩引擎，以及12＋2個最終版面重驗通過。未新增 skip、放寬斷言或增加測試 timeout。完整 CI 對最後 HEAD 重跑，結果以 PR checks 為準。

未mock Pages 桌機與 iPhone WebKit 冒煙通過，直接比對真實 JSON 兩個比例、avg60、中位數與資料日，保留原財報、SEC、世界資料與月度圖表斷言。首次 PR 預覽只在新檔不存在時放入真實試算的126個交易日切片；它不是網站 runtime fallback，也不會取代既有發布資料。

試算使用先前由 `kline-archive` commit `4c934296a` 唯讀種子重建的320根快取，並非本次最新 Actions cache。指數由 Yahoo 取得七年日線。輸入hash、來源、最新值與十月前後數值見 [proof](../docs/research-verification/market-warnings-proof.json)。CSV 前瞻紀錄沒有拿歷史回算補寫。

限制：320根個股快取無法提供400個有效家數觀測，初次分位數為null；三年日期區間不表示三年都有股票值。研究p值未重新驗證，現行名單有存活者偏差。21日累計／有效股票聯集，與研究每千檔每日不是相同單位；沒有調參追求19%／40%。

截圖為合成數值的版面測試，展示20日切換；初始60日有獨立斷言。見 [桌機](docs/screenshots/market-warnings/desktop.png)、[390px WebKit](docs/screenshots/market-warnings/iphone390.png)、[375px WebKit](docs/screenshots/market-warnings/iphone375.png)。這是 Playwright 模擬，不是實體 iPhone Safari 驗證。

永久行情／財報、archive分支、登入／帳號、Supabase函式／SQL、GCP與原價格監控均未修改。未新增通知。

## 台股 MIS 盤中來源（2026-11-05）

Primary self-review only; no independent sign-off. From latest main `3282e2fa82` (includes #224, #223, #220); merged `b10f5e7dbe` (#222) normally.
只修改 market-chart 的盤中台股來源與必要前端標示；美股、既有歷史、SQL、GCP、登入與永久資料不變。
依使用者提供形狀建立離線假資料，不連線真實證交所；維護者自行部署。
新增按市場代號驗證、40 檔分段、15 秒快取、1 秒節流、2 秒逾時、跨日清空、空價沿用及 MIS-only 今日棒；
Yahoo 與 #224 補洞照舊，失敗完整退回。前端沿用 LiveQuoteFeed，保留 TPEx 歷史來源並顯示 MIS／Yahoo 實際來源。
操作、欄位與部署順序見 [market-chart README](../supabase/functions/market-chart/README.md#台股盤中-mis維護者部署後生效)。
本地 lint／typecheck、152 檔／1,909 單元、Deno check／lint、一般與 Pages 建置通過；合併後 111 個受影響案例與標籤修正後 27 個報價案例通過。
桌機、390px／375px Chromium／WebKit 共 10 張截圖位於 docs/screenshots/twse-mis；375px Chromium／WebKit 40 個與 IPO 就緒五種配置全部通過；完整 CI 最終結果列於 PR。IPO 測試只增加頁面就緒等待，沒有放寬斷言或逾時。自動化不代表實機 iPhone 簽核。

## PR-C：永久封存優先（2026-11-04）

Primary self-review only; no independent sign-off. Branch starts at main `c3668287ae`; review revision merged main `5d2a99b720` without rebase or force-push.
C1～C5 的程式、來源退路、快取與分割基準、可見訂閱、測試及 25 張桌機／手機截圖，見 [PR_C_ARCHIVE_FIRST.md](docs/PR_C_ARCHIVE_FIRST.md) 與 [ADR-026](docs/architecture/ADR-026-archive-first-history.md)。
沒有修改登入／帳號、Supabase 函式／SQL、GCP 或永久行情／財報；archive 分支唯讀。
審查修訂通過 149 檔／1,845 單元、99＋50 瀏覽器、lint／typecheck／一般與 Pages 建置；完整 CI 另跑。
真實 Pages 的文件離開後 fallback race 已補 lifetime／BFCache 測試，原 smoke 以最新資料桌機／WebKit errors0 通過；完整 CI 於修正後 head 重跑。
個股 index／404 使用既有 IndexedDB 按日快取，不下載 manifest；#224 補齊日 K 依交易日比對。#219 全功能保留。
原非選股站點的離線失敗已在未改前端的基準重現：11 個 shell 資源缺懶載入圖表。補 public JS/CSS 預載並保持選股站點不快取策略；不放寬既有離線測試。
實際最終檢查計數記於 PR；目前沒有部署、合併 PR 或執行正式補檔。

## PR-D：交易日曆（2026-11-05）

Primary self-review only; no independent sign-off. Branch starts at latest main `0cb7789772`.
D1 只新增 XTAI／XNYS 的 generated 日曆、既有 LiveQuoteFeed 的交易時段判斷及公開盤前盤後收集的休市 gate。
細節、來源限制與驗證見 [PR_D_MARKET_CALENDAR.md](docs/PR_D_MARKET_CALENDAR.md) 和 [ADR-027](docs/architecture/ADR-027-market-calendar.md)。
原有 15 分鐘一般收盤寬限與日曆失敗的星期／時段退路保留，提前收盤精確停止。
沒有改登入／帳號、Supabase 函式／SQL、GCP 或永久行情，沒有手動執行正式排程、通知、合併或部署。
審查修訂已 merge main 5d2a99b720，獨立 market-calendar.yml 與 vars 開關已完成。143 檔／1,809 單元、建置、78＋36 瀏覽器與 8 Python 通過；完整 CI 另跑。
本地完整檢查與五種桌機／手機配置的結果列於 PR；瀏覽器自動化不代表實機 iPhone 簽核。

## PR-A：報價與圖表介面 (2026-11-04)

Primary self-review only; Claude review remains with the maintainer. Based on main `b0ca518232`.
A1: timestamp restored to the existing list toolbar; one unit and five browser cases pass.
A2: shared regular-session status, with 19 related unit and 30 browser cases passing, plus typecheck. Extended prices never supply the regular trade timestamp.
A3: direct extended quotes, in-flow source details, <=56px list rows. Public Nasdaq responses confirm a missing newly priced issuer; parser and synthetic actual-shape fixtures fixed. Seven Python and 17 related unit tests pass; 50 affected browser executions and 20 numeric-P/E layout cases pass across all five targets.
A4: empty legacy import UI disappears; importable records are offered only in the My footer. Five new unit cases plus 18 related units and 45 browser cases pass. Existing opt-in import, cloud-only data preservation and account isolation assertions remain.
A5: price-alert shortcut commits a canonical horizontal drawing through existing history/cloud sync. Checks cloud grant and successful synchronization, loaded symbol/timeframe and duplicate lines. Five new unit cases (including atlas.save/undo integration) and 20 browser cases pass; all cloud endpoints and notifications are fake in tests. No protected server/auth/SQL/GCP or permanent-record file is edited.
Full lint, typecheck, normal + Pages builds and 142 Vitest files / 1,797 tests pass. The broader affected site suite passes all 141 cases. The existing provider/cache/local-alert/drawing journey passes all 16 cases on desktop and mobile Chromium, iPad and iPhone WebKit. Final source-metadata retention also passes lint/typecheck, 23 focused units and both builds. Original assertions remain, except exact changes required by the requested new status text and removal of the empty legacy prompt; those now assert their new locations/absence explicitly. No skip or timeout was added.
The first full CI site sweep found two phone header-height failures (131.1875px against the unchanged 124px cap), with 836 passes and five pre-existing skips. Explicit compact line heights preserve fonts/content and satisfy the existing cap. The focused five-target run passes 44 cases with one existing desktop skip; the unchanged full CI and downstream offline integrations rerun on the follow-up head.
See `docs/PR_A_UI_QUOTES_ALERTS.md` for exact scope, checks and screenshots.

## Current follow-up: startup, compact watchlists and public market details (2026-11-04)

Primary self-review only; no independent sign-off. Based on latest main `df29c9351`.
The explicit ten-item request is documented in `docs/STARTUP_WATCHLIST_MARKET_DETAILS.md`.
No protected auth/account/Edge Function source, SQL, existing permanent market records or archive branch is edited.
New display snapshots are bounded, read-only consumers of public sources; source dates are never invented.
Verified locally: clean npm ci; lint; typecheck; 137 Vitest files / 1,774 tests;
normal + Pages builds; 5 public market-extras Python tests; 33 generated-branch tests;
36 final browser cases across desktop Chromium, mobile Chromium and iPhone WebKit.
The wider 237-case regression run had 233 passes and four findings (including a fractional
43.999996px touch-target measurement). The accessibility label, WebKit padding and 45px
compact-control fixes are verified without weakening
assertions (80% main viewport, 44px controls, <=52px rows). Existing auth, holdings, import,
navigation, live quotes, PWA and stock-view cases in that wider run passed.
Real-source smoke in an external temporary directory obtained AAPL's after-hours trade timestamp
and an honestly unavailable premarket observation; no permanent records were written.
Final semantic review also separated Nasdaq expectedPriceDate/pricedDate from listing dates; the five Python cases, 14 focused unit cases and 12 stock/IPO browser cases passed again.
Final boundary fixes keep hyphenated share classes (BRK-B) eligible for same-time volume, and preserve a known startup failure after the 15-second waiting timer. Their unit/browser assertions are included without relaxing the gates.
The first complete CI browser gate (804 cases) reported 786 passes, five existing skips and
13 findings: old caption/footer/theme/error assertions plus one premature WebKit quote-menu
interaction. Tests now assert the new exact footer locations, storage-to-health adjacency,
compact picker/chart alignment, distinct missing/invalid reasons and a settled NVDA chart.
The affected 78-case suite passed 77 with its existing desktop-only skip. No assertion was
removed or broadly relaxed; no new skip was introduced.
Optional extended quote fields also tolerate null/zero/malformed metadata without rejecting otherwise valid regular bars; the complete 1,774-unit suite, lint and both builds passed after this compatibility fix.
CI will verify the exact pushed head. No merge or deployment is authorized.

## Current follow-up: timeframe fixation, calibrated breadth and list evidence (2026-11-02)

Primary self-review only; no independent sign-off. Based on main `133220a01`.
On November 4, integrated main `04feb00d2` into this feature branch to resolve the three
workflow/review/CSS conflicts. Both sets of styles and review entries remain. Current selector
ranking, 2-year scores, selector v2, live price updates and update timestamps are preserved;
their implementation remains identical to main outside the existing recommendation wrapper.
The previous feature head `167ab294a` passed all four CI workflows, including the complete site,
disposable PostgreSQL and offline integrations. The merged head is verified again separately.
After integration, lint/typecheck and both builds passed; all 1,759 tests in 135 Vitest files
passed with one worker. An initial concurrent-build run exceeded the unchanged 5-second SEC
lineage test limit; rerunning without the competing builds passed, with no test or timeout edit.
All 84 related desktop/mobile Chromium and iPhone WebKit cases passed, including selector v2 and
live quote refresh/update timestamps. Live quote coverage is also included in the PR CI sweep.
The diff against current main contains no auth/backend or permanent market-record changes.
The two implementation subtasks followed the model allocation in the user's attachment;
their implementation checks are not represented as independent review.

Existing v4 symbol/timeframe ownership and storage authorization remain. The indicator panel
and recommendation popover expose the current timeframe's existing MAs and drawing locks.
Batch fixation goes through AppStore's guarded indicator and drawing APIs, preserves anchors,
and excludes Volume, foreign timeframes and other symbols. Daily stock/monitor parameters remain
separate, under an explicit daily disclosure on non-daily charts. The popover's editor mounts
only while open; existing recommendation fetching and apply/cancel/fix behavior remain.

The attachment's Python rules/core are unchanged; imports, workflow wiring, and input-quality
checks are integrated separately. Quarterly/manual calibration and daily updates share the
universe publication owner and concurrency group. A bounded, fail-soft signal step installs
its own optional dependency, so errors cannot consume the whole job or block original breadth
publication. Missing, short, empty or stale index histories and excessive overlap fail before
writing. No price, financial or archive record is modified. Bootstrap stores only newly derived
aggregate history and calibration, with provenance in scripts/breadth_signals/README.md.

Real calibration requested all 5,346 website-listed US symbols, all with nonempty history.
The 5,937-day breadth sample is 2003-03-28–2026-10-30; five indices have 57–67 completed trades.
S&P 500 hit rate is 68.25% versus the same-rule arbitrary-day 47.61% baseline. Untouched generated
breadth overlaps 120 sessions with maximum MA20/60 differences of 0.4 percentage points each.
Actual sample dates and the higher Russell/SOX results relative to the attachment are disclosed
without changing rules. The real daily payload also passes the frontend contract with all five
indices. Physical trading performance and independently reproduced TW research are not claimed.

Optional breadth parsing isolates malformed indices and rejects invalid top-level data. Stale
signal dates do not label newer breadth. Numeric breadth, source/date, chart and TW segments
survive missing/invalid signals. Visible P10/P90 bounds, five percentile zones, index plans,
selectable historical markers and folded evidence use the existing SVG chart and theme variables.
TW has no calibrated trading labels; overbought is not presented as a sell instruction.

Watchlist explanations reuse the existing evaluator over each isolated condition and its complete
source, preserving fixed-MA precedence, strict/inclusive boundaries, repeated growth gates and
missing-data policy. Prior membership is not treated as historical indicator evidence. Explanations
stay in UI memory; saved definitions, collector history, last-opening baseline and cloud payloads
are unchanged. Source removal, manual removal and unavailable inputs have distinct explanations.

Clean npm ci, lint, typecheck, normal/Pages builds and the disabled-sync collector command passed.
All 1,743 unit tests in 134 files passed; the final changed-component subset passed 20 tests.
Python: 24 attachment/fail-soft tests and 63 breadth/generated-publication regressions passed.
Failure fixtures use complete calibration and assert the specific rejection reason; a successful
daily run also appends the new site session, emits all five indices and leaves source breadth intact.
Related desktop/mobile Chromium and iPhone WebKit site regressions passed all 114 cases in 8.1
minutes. Screenshots were visually inspected for chart-local controls, current evidence
and calibrated breadth; browser automation is not physical iPhone verification.
The original chart gesture/timeframe suite additionally passed 23 cases, with its existing
desktop-only touch skip, across desktop/mobile Chromium and iPhone WebKit. The Python CI matrix
now explicitly installs pytest and runs the attachment tests in both records-only and generated
modes; unittest discovery alone does not execute pytest-style function tests.
The full CI site sweep passed 754 cases and exposed three profile variants of an old volatility
test expecting daily recommendations immediately on a weekly chart. Its interaction now expands
the explicit daily disclosure before retaining the original visibility/value assertions, matching
the weekly recommendation test. No assertion was loosened or production code changed for that fix.
The four-file follow-up (volatility, recommendation, stock views and layout) passed all 30 cases
across desktop/mobile Chromium and iPhone WebKit; targeted lint also passed.

Protected auth/account/backend files, outer navigation, permanent K-line/price/financial records,
archive branch, SQL, notifications and deployments are untouched. No merge or deployment.

## Previous review: ma-selector ranking, 2-year score and selector v2 in the recommendation panel (2026-10-30)

ma-selector PR #9 added three display-only features to its web page: stock-level ranking against the 83 reference stocks, a 2-year reference score on short/medium candidates, and the experimental selector v2. The companion ma-selector branch `claude/atlas-v2-fields` writes them into the recommendation JSON (VERSION 2; production fields byte-for-byte unchanged, extras omitted without their reference files). Atlas parses each extra as optional — a malformed extra is dropped, VERSION 1 files still read — and shows: 「贏過 X%」 under each range, 「2年 NN」 on short/medium candidate buttons, and a collapsed 「實驗選線 v2（尚未驗證）」 section with toggleable candidates, suitability with ranking, and 「套用 v2 全部」 (adds only missing daily SMAs by id, as the current apply does). The current picks, fixed-parameter flow and toggles are unchanged.

Local verification: typecheck, lint, production and Pages builds passed; Vitest 128 files / 1,697 tests passed (3 new, using fixtures generated by ma-selector's own `recommend()` on synthetic prices). Playwright desktop and phone Chromium for `ma-recommendation.spec.ts`: 8 passed (2 new: ranking, 2-year scores, v2 apply/toggle; v2 without a second line). The phone range-label column was set to `nowrap` after a screenshot showed per-character wrapping. WebKit runs in CI only. No physical-device or independent review sign-off is claimed.

## Previous follow-up: Chinese heatmap, asset lists and P/E (2026-10-31)

Primary self-review only; no independent sign-off. Based on main `e60f63d8a`.

The US heatmap translates display labels only (152 current Nasdaq names plus common aliases),
leaving source IDs, grouping, weights and returns intact. Compact labels retain full tooltip/aria
names. The shared permission display now precedes data health at the foot of every page, while
login/auth code and storage policy remain unchanged. Screener virtualization keeps its existing
behavior within a near-viewport-height list.

World assets default to quote rows, with an explicit continuous-gallery toggle and the existing
complete chart workbench. The read-only provider validates source identity and quote/candle
alignment and never substitutes stocks on failure. Existing research drawing identities are
reused for account/symbol/timeframe ownership and saved MAs. BTC source/calendar guards remain.

The stock P/E time series below the full-height K-line uses the existing historical EPS rules,
publication dates, split basis and weekly prices. Unsupported or missing data fails locally;
no current-EPS retroactive history is fabricated. The footer and P/E are accessible by scrolling,
without reducing the plot's initial height. Changing stock returns the main scroller to the top.

Clean npm ci, lint, typecheck, normal build and Pages build completed. All 1,708 unit tests passed.
Desktop/mobile Chromium and iPhone WebKit new-feature checks passed (15/15). A broad existing-site
sweep ran 411 cases; it exposed duplicated Taiwan aria names, shortened stock header symbols and a
12px safe-area sizing error, all now fixed in production code. The final seven-file rerun passed
98 cases with 4 device-specific skips, including every previously failing case, legacy research
drawings, BTC source rejection, chart ownership, navigation and portrait/landscape safe areas.
Other existing financials, watchlist, screener and account-isolation cases passed in the broad sweep.
The PR checks run the full site suite, real Pages source checks and existing offline storage gates.

CI also exposed the collector's old import of the browser data facade: the added provider made
esbuild reach Vite's raw BTC CSV import. Valuation helpers are moved byte-for-byte into a shared
fundamentals module, with the public facade re-exported. The collector and pure filters now import
that module directly. The actual collector bundles/runs with synchronization disabled, and a new
esbuild regression test proves browser providers/raw assets stay outside its dependency graph.
After that extraction, the complete 130-file unit suite, both builds, lint/typecheck, the real
disabled-sync collector command and 36 additional desktop/mobile/WebKit P/E and screener cases passed.

No permanent market files, authentication implementation, Supabase functions, SQL or deployment
configuration changed. No merge or deployment is performed. Physical iPhone testing remains a
maintainer check; automated WebKit is not claimed as physical-device verification.

## Previous follow-up: full-market breadth and chart review (2026-10-30)

Primary self-review only; no independent sign-off. Based on main `5910a85e0`.
Production breadth now reads the existing full-market listing/cache reader and
uses the same listing age/size and 90% coverage gates as entry-success rates.
MA20/60 math and legacy analysis CLI remain. Entry-success calculations are
unchanged. Coverage failures retain the prior output, and daily no longer owns
or overwrites the breadth output; only universe publishes it to generated.
No price/cache/archive inputs are written by the new collector.

Global labels and rate headline widths are adjusted without changing yield,
policy-bound or reasonable-band calculations. An explicit single minmax(0,1fr)
rate-card grid track prevents old canvas dimensions from becoming a minimum
width after a desktop-to-phone resize. This keeps the no-overflow assertion.

StockTabs can host the existing recommendation and volatility floating menus.
Scoped selectors exclude all popup form buttons; the compact original header
height, chart lifecycle, candidate apply/cancel and original grouped-parameter
editing stay intact. Screener review reuses StockChartList after the existing
filter/search/sort pipeline, including saved-MA precedence, six-month range,
lazy paging and offscreen chart disposal. It never evaluates/saves a watchlist.

The research headline uses only the published full-market rsRank, displaying
its original date/benchmark independently of the comparison controls; absent
rankings are not estimated. Existing cumulative-return comparison remains.
Theme charts opt into the existing ChartEngine histogram and real symbol volume
formatting. Daily/weekly/monthly bars still come from the existing provider and
aggregator; other DrawableCandleChart callers retain their original default.

Local checks include clean npm ci; 1,696 tests in 128 Vitest files; 97 Python
breadth/full-market/closed-session/generated-publication tests. Lint/typecheck
and normal/Pages builds pass. Final related site subset: 71 passed / 1 existing
skip; normal drawing/timeframe regressions: 21 passed. The broader site checks
also cover all new chart/RS/volume flows; initial layout failures were fixed
without loosening their bounds and verified by the final subset. Browser
coverage includes desktop, mobile Chromium and iPhone WebKit with mocks, not a
physical-device or live-price claim. Python outputs/journal tests use temporary
files. The Actions real-cache breadth check writes only to /tmp and never saves
cache or publishes outputs.

Protected auth/account/server files, outer site navigation, permanent price,
financial and K-line records, archive branch, SQL and notifications are untouched.
No merge or deployment.

CI navigation follow-up: the WebKit trace showed a metadata heading followed
by reload before the lazy FX candle effect started; fetch then ran on an
unloading document. The research reload/back regression now requires validated
OHLC before each navigation (and checks commodity OHLC too). Its strict empty
page-error assertion is unchanged; no error filtering, timeout increase, retry
configuration or production fetch behavior was changed.

## Current follow-up: bond layout, temporary screener conditions and drawing editor (2026-10-30)

Primary self-review only; no independent sign-off. Based on main `3334513d5`.
JP and US share the three-number headline component and summary/band/spread
ordering. The BOJ point target is displayed as a point, with its nature disclosed;
no bounds or missing history changes are fabricated. The five global views use
short labels and a five-column phone row with 44px targets. US scenarios and ETFs,
all routes, data loaders, models and sources remain unchanged.

Temporary screener filters now come only from the explicit URL. Group/market
switches clear conditions and close the old draft. Fresh visits no longer restore
old per-market values. Explicit shared/back/reload URLs and saved watchlist rules
still work; existing preference data and import/export helpers are retained.
There is no timer clearing a user's ongoing work.

The stock drawing dialog focuses Cancel before the native color field. A
successful settings commit closes the quick editor through an optional callback;
validation errors remain visible. Non-quick panels and the shared/account dialog
are unchanged. Canonical anchors, gestures, lock guards and storage schema/queues
are unchanged. No auth/account, protected server, shell-navigation, permanent
price/financial/K-line, SQL, notification or deployment changes.

Actual local validation: clean npm ci, lint/typecheck, 1,694 Vitest tests in 128
files, normal and Pages builds; final 60 site-browser and 21 normal drawing/
timeframe tests passed across desktop/mobile Chromium and iPhone WebKit. A wider
site run passed 158 cases with one existing desktop skip, and exposed six copies
of the former P/E auto-restore expectation. These now require a fresh visit to
clear filters and an explicit URL to restore them; the final run includes all 15
P/E tests passing. Original validation, sharing and saved-strategy gates remain.
The updated 320/375/390px five-view layout regression is included in CI.

See [behavior and verification](docs/BOND_FILTER_EDITOR_FOLLOWUPS.md).
Headless WebKit is not physical-iPhone certification. No merge or deployment.

---

## Previous follow-up: market sections, US/JP yield bands and drawing menu (2026-10-29)

Primary self-review only; no independent sign-off. Based on main `8d360e149`.
Market content navigation combines TW/US/JP/KR/EU under 大盤 while retaining each
country selector, route compatibility and last-country memory. 全球 and 題材 stay
separate. No application-shell or bottom-navigation changes.

Japan's new global view shows 10Y−BOJ spread/history and the requested reference
band, without ETFs or scenarios. US adds the same band while keeping its previous
content. The supplied coefficient snapshot and JS formula are versioned; Python
OLS uses residual n−2 and spread sample n−1, completed months only, full history,
and per-country failure isolation. Recent BOJ effective dates were checked against
official statements, with the older attachment dates disclosed. Original models
and dates survive unavailable, truncated or unreleased monthly source data.

The existing daily/rates workflows produce only two new generated reports, with
their existing owner/concurrency protections. No new writer or external API in
the browser. Read failures show a dated attachment baseline, never a fresh quote
or fabricated history. A live read-only collector check returned official JP
2026-10-28 10Y=2.867%, BOJ=1.25%, spread=1.617pp, with no missing MOF source.
It wrote review output outside the repo, not permanent records or hosted reports.

New drawings use 2px unless a custom default exists. Existing styles/ATR, canonical
anchors, commit-on-release, lock/pan ownership, history and storage remain. Only
selection/release emits a transient host-local point for a clamped DOM menu;
pointer movement does not update React or persist pixels. Menu coordinates include
the legend's offset and prefer outside the projected shape to leave its other
handles/body reachable. Lock/delete/settings/deselect have 44px targets, settings
reuse the editor, and mobile no longer consumes a bottom row. Gesture instructions
also float inside the plot, keeping its height stable before/during/after drawing.

Actual local checks: clean npm ci; lint/typecheck; full Vitest 1,694 tests
in 128 files; 12 new Python tests plus 49 existing rates and 33 generated-storage
tests; normal/Pages builds; 122 site-browser checks passed with one existing
desktop skip for a phone-only sheet. Final code passed 56 normal drawing/gesture/
storage/timeframe tests (one original desktop skip for mobile touch), and 44 site
tests (one original desktop skip for a phone-only sheet), across all three profiles.
These retain corner/body edits, locked native pan, undo/redo, symbol/timeframe
ownership, V3 import/V4 export and ATR coverage. New tests assert 2px, the menu's
pixel position/44px targets, unchanged plot height before/during/after placement,
and lock/edit/delete. No gesture assertions or test timeouts were weakened.
The cold lazy-navigation/old-deployment recovery helper now selects 大盤, with
both original recovery scenarios passing on all three profiles (six tests).
Full Python CI exposed an existing synthetic full-market size fixture that
explicitly omitted 2026Q3, while the real 2330 file now contains that quarter.
Newer Yahoo data correctly beat that older official fixture. Only the synthetic
coverage is completed; source precedence and all source/size assertions remain.
All 40 growth tests pass; no production financial implementation or record changes.

No dependency/lockfile, account/auth, protected server-function, permanent price,
financial or K-line archive changes. No SQL, notifications, deployment or merge.
Headless Chromium/WebKit is not physical-iPhone verification. Details and sources:
[MARKET_BONDS_DRAWING_CONTROLS](docs/MARKET_BONDS_DRAWING_CONTROLS.md),
[ADR-023](docs/architecture/ADR-023-market-bonds-and-drawing-menu.md).

---

## Previous follow-up: preserve the original stock MA source (2026-10-29)

Primary self-review only; no independent sign-off. Based on main `b77a4a141`.
The previous dialog only read/wrote the dormant ungrouped row while the chart
never hydrated stocks settings; the monitor gave that new row priority over
original grouped values. All consumers now resolve original grouped stock rows
before dormant duplicates, with the lowest grouped ID as a deterministic
fallback for legacy aliases. No membership or notification consent is added.

Fixed editing opens the original saved values first and explicitly offers the
currently applied chart values as a replacement. The original ID/ticker/group
are retained on confirmation. Disclosed dormant duplicates are deleted only
after the primary save succeeds; partial cleanup reports an error and retries
start with a fresh read. Changed/missing originals and account-context switches
stop follow-up writes. This uses the existing owner-scoped API; no SQL, protected
server functions or account/auth implementation is changed or deployed.

Chart hydration adds daily SMA instances with stable source IDs and 2px width;
manual/locked indicators, other timeframes, drawings and view state are preserved.
An unchanged source fingerprint does not override interactive toggles. Previews
are read-only and prefer saved stock values to selected-list MAs; their initial
and reset range is six calendar months from the last actual bar, with full
history retained for warmup/zoom. The watchlist-only index strip is removed.

Validation evidence and operational limits are recorded in
[STOCK_PARAMETER_CONSISTENCY](docs/STOCK_PARAMETER_CONSISTENCY.md). Browser tests
exercise the real chart canvas, grouped/dormant persistence, cancellation,
failed writes/loads, independent stock MAs and six-month zoom/reset. Existing
account privacy, console, navigation, signal, market, drawing/timeframe and
indicator behaviors remain regression gates. Headless WebKit is not a physical
iPhone verification. Production records are unchanged until a user confirms an
edit in a maintainer-published build.

Actual local results: clean npm ci, lint/typecheck, normal/Pages builds;
1,686 Vitest tests in 127 files; 61 relevant Python tests; 180 site-browser
regressions, followed by final 39-case canvas/import and 21-case import reruns,
all passing across desktop/mobile Chromium and iPhone WebKit. The normal
1D/1W/1M scope test passed all three platforms: one initial WebKit global
45-second timeout under concurrent browser work was rerun alone unchanged and
passed in 19.2 seconds. No assertions or timeouts were weakened. Original
account/auth and protected Edge Function paths, lockfile and permanent market
records are untouched. Legacy import retains the account's values and now
avoids reintroducing grouped/dormant duplicates for normalized ticker aliases.

---

## Current follow-up: price-axis isolation and watchlist chart review (2026-10-29)

Primary self-review only; no independent sign-off. Based on main `3cece7a70`.
Native price-axis dragging disables autoscale on the reused candle series. A
real-canvas browser reproduction switched synthetic 20-price AAPL to 2,000-price
NVDA: the old engine showed correct OHLC but zero candle-colored pixels. Public
price-scale applyOptions re-enables autoscale on symbol/timeframe changes and
explicit Reset View. Same-context refresh, saved logical ranges, canonical
drawings and per-timeframe indicators are retained.

My Lists selects the default existing list and exposes a controlled selector
immediately below the source tabs. ConsolePage continues to own definitions,
evaluation, history and synchronization. The old lower selector is moved; recent
search is removed only from this board, with global search/history preserved.
The chart-review button uses every current row, automatic 20-row batches,
offscreen chart disposal and native vertical/wheel scrolling. MiniChart/provider
are reused without preset stock MAs or new persistence.

The four stock tabs now share compact styling between the workspace and financials.
Financial identity comes first, period/display controls remain, and folded secondary
queries sit in the tools row. The original 112px mobile header budget and scrolling
header tests remain unchanged and pass. No account/auth, Supabase, navigation-shell
or permanent market-data files are changed; existing MEXC and monitor behavior remains.

Actual local validation: clean npm ci; lint/typecheck; 1,677 unit tests in 126 files;
normal and Pages builds; 209 affected desktop/mobile Chromium/iPhone WebKit site
tests passed with one original desktop skip for a phone-only sheet; three normal
browser timeframe/drawing reload/export tests and three global-search/history
checks passed. New tests compare rendered candles, top selector order/defaults,
60-row chart review and the shared stock-tab measurements. Older list tests now
assert option counts and dropdown selection while retaining their functional gates.

No new dependencies, SQL, server deployment, notifications or merge. Synthetic
prices and fake services only; headless WebKit is not physical-iPhone certification.
See [the implementation notes](docs/CHART_WATCHLIST_FOLLOWUPS.md) and
[ADR-022](docs/architecture/ADR-022-price-scale-and-watchlist-review.md).

## Previous extension: Discord monitor signal groups and screener health layout (2026-10-28)

Primary self-review only; no independent sign-off. Based on main `7dfa54650dee5b476f1d54da8ec337cf49b5271c`.
The watchlist board keeps its current default and adds 突破／跌破 beside LINE 熱門.
Group details coalesce triggered MAs per stock, show daily candles/volume with existing
MiniChart, preserve vertical/wheel page scrolling and button/pinch zoom, automatically
load further cards and release offscreen canvases. Existing chart wheel behavior stays
the default elsewhere. Back navigation restores the group heading's focus and position.

The independent `monitor-signals` function reads only the verified cloud owner's
existing last-successful Discord state. None/device are denied server-side before
private reads. Shared identity/grant helpers are reused, not changed; user-settings,
chart-drawings, login/account files, app shell and monitor producers are untouched.
Pagination has an explicit cap, output uses a field allowlist/no-store, account changes
cancel old requests, and personal results stay in RAM. Body size and upstream lifetime
are bounded. The page shows only each market's latest signal session (exchange-local
date) from a 30-day last-state read, not full notification history.

The screener container now grows with its content while the virtual list remains a
bounded scroll region. Synthetic 390×520 comparison reproduced 214px of overlap in
the old CSS; the fixed layout has a 34px gap. Browser assertions check geometry,
virtual scrolling, disclosure access and the final health row within main.

Actual local gates: clean npm ci, lint, typecheck, 1,636 unit tests across 125 files,
normal/Pages builds, and 156 broad desktop/mobile Chromium/iPhone WebKit regressions
passed. Twenty new unit cases include actual Edge entrypoint execution with fake
LINE/database responses, ownership/grants, malformed inputs, paging/truncation,
revocation and context races. The final shared-chart/scroll/focus run passed 53 cases;
its WebKit long-list case passed after correcting the test to address the price-pane
canvas and using supported DOM touch events (54 cases covered). The negative control
requires horizontal drag to be handled while vertical touch remains uncancelled.
Preview bundles do not contain the server-role environment key name.

No production data was queried, no SQL executed, no notification sent, no deployment
or merge performed. New function enablement is a maintainer action; missing service
shows an error/retry without changing saved lists. Headless WebKit is not a physical
iPhone sign-off. See [the runbook](../docs/ATLAS_MONITOR_SIGNALS.md) and
[ADR-021](docs/architecture/ADR-021-monitor-signals-read-only.md).

## Previous feature: MEXC read-only synchronization, PR B (2026-10-28)

Primary self-review only; no independent sign-off. Started from main 61eb973 after
#199 merged. Cloud users can enter a MEXC key pair on the website. The server checks
the official key permission endpoint, accepts only known read scopes, and performs
three fixed-origin GET requests. It never trades, transfers or withdraws. All tests
use fictitious keys and a fake MEXC transport; no real MEXC API call was made.

AES-GCM encrypts BOTH credentials with a server-only 32-byte environment key and
owner/account/version AAD. Only last-four metadata and sanitized balances/quotes
return to the browser. Payloads are bound to their storage context BEFORE awaiting
identity initialization, preventing a key or holding write from moving to a newly
selected account. No auth files are changed. Private tables/RPC remain service-only.

Server rate limits, post-I/O grant checks, shared owner locks and one-use claims
prevent rapid retries, revoked writes, stale-cost replacement and resurrection
after disconnect. Unknown/new/increased costs remain staged, not zero-cost profit.
Confirmed averages survive decreases; quotes stay per account. No daily P&L is
invented from rolling 24h values. Visible-page five-minute polling pauses during
review, editing and hidden pages. Existing manual imports and navigation remain.

Validation before PR: clean npm ci; lint and typecheck; 1,656 Vitest tests / 124 files; both builds; 150 affected browser regressions, followed by 63 storage/holdings/MEXC checks and 24 final MEXC checks across desktop Chromium, mobile Chromium and iPhone WebKit. Static SQL parsing passes. Frontend bundles contain no server master-key lookup or MEXC upstream client header.

New SQL was statically parsed with pglast, not executed. Relational-fake tests execute
the actual handler/crypto, not actual PostgreSQL RLS/locking. Maintainers must verify
and manually apply SQL, configure HOLDINGS_ENCRYPTION_KEY, deploy user-settings, then
publish the frontend. See [the runbook](../docs/ATLAS_MEXC_SYNC.md). No deploy or merge.

## Previous feature: holdings import and P&L, PR A (2026-10-28)

Primary self-review only; no independent sign-off. The 自選 console retains its
default list view and gains 清單 / 持股. Browser-only CSV/XLSX/paste parsing feeds an
editable preview and account snapshot replacement, with saved column-index mappings
and explicit missing-cost / non-trade / incomplete-history review. All fixture values
are fictional. The legacy official KGI inventory image has no cost column; tests
therefore require correction instead of inventing an export cost. Firstrade transaction
files reconstruct average cost, including partial/full sales and net purchase fees.

Quantities and costs are decimal strings, prices use existing providers, and missing
quotes/FX remain explicit partial totals. USDT is never silently treated as USD.
Known ETF flags come from the official catalog. Mobile controls retain 44 px targets.

Storage follows the existing verified context: guest RAM, device account-qualified
userStorage, cloud Supabase plus RAM only. Account changes invalidate old operations
and unmount raw imports. Normalized positions alone use fixed-URL POST, no-store
responses and sanitized errors; raw files, extra columns, positions in URLs/logs or
analytics, fallback local cloud saves, notifications and broker connections are absent.

Every holdings server action follows requireCloudStorage; verified owner is bound
inside a service-role-only RPC. SQL declares RLS, explicit privilege revocation,
owner predicates, transactional account replacement, per-owner serialization, row
caps and revision checks. Per the user's instruction NO SQL was executed; handler
tests use the existing relational-fake style and do not certify PostgreSQL locking
or RLS. Maintainers must review/apply SQL and verify permissions, then deploy the
function, then release the frontend. See [the runbook](../docs/ATLAS_HOLDINGS.md).

The branch was rebased onto main `13c1ab5d58db209f1506fdd75cede34ac62e3e67` after #197 merged; both data status and its comparison features remain intact. Clean npm ci, lint, typecheck, 1,616 unit tests / 123 files, both builds and 147 targeted desktop/mobile/WebKit integration tests passed before opening the PR. An additional 27 holdings browser checks passed after adding preview action guards (finish or cancel before switching/reloading account actions).

No merge, deployment, permanent-price/history modification or MEXC call. PR B starts
only after A is merged. Actual clean-install/build/unit/browser checks are recorded
in the PR; headless WebKit is not a physical iPhone or real-broker-file certification.

## Previous extension: 資料狀態與三項公開資料比較（2026-10-28）

由最新 main `fe7b39f50348b55241056ccd373b4feab2530a04` 開分支。

- 新增「我的 → 資料狀態」，18 個來源與八個 workflow，前端僅讀約 10 KB 的 `data-status.json`；來源政策集中在 Python `SOURCES`。交易所日曆包含 TW／US 休市、早收盤、夏令時間與收盤後發布寬限。
- 圖表研究新增個股與大盤價格報酬比較；選股新增兩日同條件名單進出；題材頁新增產業同期間報酬、上漲比例與完整共同樣本量比。
- 新增 generated-only producer、owner 與忽略路徑。沿用既有 owner delta／非 force 重試；讀取 archive manifest 與還原的 rolling cache，無永久來源寫入。選股歷史快照首次寫入後不覆寫，每個市場只保留最近 10 個資料日（衍生輸出，不列入 append_only），索引可更新。
- 原始功能只加入四個入口。登入／帳號指定路徑、Supabase functions、網站外框、永久價格／財務檔、archive 分支均沒有修改；無通知、合併或部署。

驗證：`npm ci`；1,550 項 Vitest／119 檔；lint、typecheck、正常 build、Pages build；Python 16 項新分析測試與 33 項 generated storage 測試。以實際 generated 文件與 archive manifest 唯讀解析，18 來源皆有有效日期，八個 workflow API 皆取得紀錄。新增 status owner 的真實 disposable Git 測試證明 concurrent daily 更新保留、main 永久記錄不動、舊快照不可覆寫。完整瀏覽器回歸 179 通過／4 項既有平台略過；追加歷史價格口徑保護後，受影響的資料狀態、比較、選股條件與本益比 57 項全部通過。

已接受限制：歷史名單從功能上線後開始保存，需兩個資料日，不倒填財務；固定均線採目前設定對齊歷史行情，要求收盤與快照同價格口徑，不一致或參考缺少時停止比較。狀態監控已發布衍生檔，不直接輪詢原始 TWSE 等端點；季度擷取時間不是財報期別，date-only 不捏造時分。archive 日期為市場最大值。產業成員採目前分類、具有存續偏差；缺快取的量能顯示未知，不等於零。實體 iPhone 操作未驗證；沒有獨立外部審查簽核。

詳細門檻與口徑：[資料狀態與比較功能](docs/DATA_STATUS_AND_COMPARISONS.md)、[ADR-018](docs/architecture/ADR-018-data-status-and-public-comparisons.md)。

## Previous follow-up: iOS top-level LINE app launch (2026-10-27)

Real-iPhone report on #185: the hidden `line://app` iframe never opened LINE from the
home-screen PWA (iOS WebKit ignores custom schemes in subframes). iOS/iPadOS now
activate a disposable `line://app/{LIFF_ID}?intent&write` link (top-level) inside the
same synchronous gesture; only `line://app/` may be navigated, never http(s), no
reload or window.open. Android keeps the subframe: LINE documents no `intent://` form
and a top-level failure could replace the receiver. If the page is still visible and
focused ~1.5 s later (no hidden/blur/pagehide), the existing manual link becomes a
prominent button with a hint; nothing is auto-opened. Proof scope, reverse code,
network binding, TTL, one-use claim and server grants are unchanged. Browser tests
stub the scheme click and assert no main-frame navigation/pagehide. No server change.

## Previous follow-up: iOS launch sheet and receiver focus (2026-10-27)

Primary self-review; no independent sign-off. #184 was already merged, so this is
a separate PR from current main. Mobile user gestures dispatch a legacy line://app
URI in a disposable child frame, never navigating the RAM-only receiver or opening
a window first. LINE's official deprecation and takeover warning are explicit in
[the runbook](../docs/LINE_LOGIN_LAUNCH.md); this is not described as a newly supported
secure app-link scheme or as headless proof of iPhone native dispatch. Existing
reverse code, network binding, read/write proofs and server grants remain mandatory.

Fallbacks load a real static-first HTML entry before offering the official HTTPS
LIFF link; close failures leave readable X instructions. The helper gets its LIFF
ID from the build, ignores query-selected IDs/redirects, strips its own query, uses
no-referrer and never polls, initializes SDK or writes storage. Original pending
receivers scroll/focus the code field on foreground and on late readiness.
Tests model OS dispatch without claiming a real app launch; they cover no primary
popup/navigation, frame cleanup, wrong code/network regressions, focus, blocked
close, no-JS explanation and malicious target rejection. No SQL, Edge Function,
production settings, merge or deployment. Actual test counts are in the PR.

## Previous follow-up: reverse confirmation and same-network binding (2026-10-27)

Accepted the reported approval-phishing gap: a visual pairing warning alone cannot
stop a receiver who convinces a visitor to approve its link. The original receiver
must now manually enter a separate six-digit code shown only after verified LINE
completion. Server randomness uses unbiased rejection sampling; the row stores only
a secret-salted, domain/intent-bound hash. Empty readiness polls neither reveal the
code/identity nor consume attempts. SQL row locking counts wrong codes and deletes
on the fifth; cancellation returns no ciphertext. Existing read/write proofs, visual
pairing code, TTL, encryption and final account/grant confirmation remain required.

Begin and complete bind an independently domain-salted IPv4 /24 or IPv6 /64 hash.
Only CF-Connecting-IP from the current hosted Supabase ingress is accepted. No
X-Forwarded-For / X-Real-IP fallback; malformed/missing values and Cloudflare's shared
cross-zone Worker sentinel fail closed. Read-only GET probes confirmed this actual
endpoint rejects a forged CF header while a forged XFF survives; function ingress
logs were queried only for booleans, never client IPs/tokens. Primary official-source
references, evidence table, scope and network routing limitations are in
[the handoff runbook](../docs/LINE_LOGIN_HANDOFF.md). Shared NAT/VPN membership does
not prove identity; neither control is claimed to stop disclosure of the reverse
code to a phisher. LINE approval explicitly says only to approve a login just
initiated on one's own device.

The manual SQL supports old schema upgrades and repeated application. New columns
are nullable for legacy rows, which fail closed and must restart; the six-argument
RPC overload is removed. Permissions stay RLS/private/service-only SECURITY INVOKER.
No production schema/function/frontend deployment, merge, grant or personal data
change was performed. SQL -> function -> frontend remains a required rollout order.

Native disposable PostgreSQL covers concurrent wrong guesses, limit/deletion,
legacy rows/overload removal, missing/different networks and one-use consumption.
Browser regressions execute the real shared handler and cryptography with simulated
trusted ingress, plus the pinned real SDK in separate storage partitions. They cover
same-device flow, correction of a wrong code, five guesses and network rejection.
Actual test counts and exact-head CI are recorded in the PR. Primary review only;
no external independent sign-off or physical iPhone/real LINE consent claim.

## Previous follow-up: real SDK storage partitions and one-time LINE handoff (2026-10-26)

The official SDK 2.31.2 reproduced the reported loop: its own partition exchanges
the callback code, while a new browser context with the same callback has no PKCE
verifier and remains logged out. Earlier account-return doubles did not model
that behavior. The deployed root also redirected before primary SDK init.

Mobile / standalone login now uses a separate LIFF window and a two-minute
one-use handoff. Receiver and sender proofs are separate; only hashes are stored.
The sending page requires an explicit top-level approval and matching visual
pairing code before transferring anything; merely visiting a link does not
complete a handoff, and framed approval is rejected. The visual code is not an
authorization key. The verified ID token is AES-GCM encrypted with a server-only key and bound to
its intent. The receiver confirms the account and still needs the normal fresh
access.get grant. No URL or candidate identity can grant persistence. Private
RLS storage, service-only invoker RPC, bounded capacity, expiry and atomic
consumption are tested against native disposable PostgreSQL. The root now uses
the same Atlas bootstrap; desktop SDK fallback and guest RAM-only behavior stay.

Real SDK browser tests use test ECDSA keys and provider HTTP fixtures, including
two completely independent browser contexts. Existing SDK fallback tests now
explicitly model a desktop user agent; mobile/PWA behavior is exercised in the
new real-SDK handoff suite. No authentication assertion was relaxed. Official
provider consent / physical iPhone return is not claimed.

Read-only public deployment/configuration and aggregate server logs were checked.
Production advisories showed only the existing no-policy RLS informational
notices ([reference](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)), matching the service-only policy. No production schema, grant, function
or data was changed. SQL -> function -> Atlas entry publication is an explicit
rollout prerequisite in [the handoff runbook](../docs/LINE_LOGIN_HANDOFF.md).
Primary review only; no independent external signoff. Actual checks are in the PR.

## Previous follow-up: LINE login handoffs and bounded explicit startup (2026-10-26)

The pre-router gate recognized only OAuth code/error returns with a state query
parameter. It missed LIFF primary liff.state/referrer and credential-fragment
handoffs, which the query router could normalize away before SDK initialization.
It also imposed a state requirement that the SDK's code/PKCE processing does not
require. Supported LIFF markers now reach SDK init before routing; consumed credential
fragments are then removed without retaining them in a canonical route; arbitrary
unmarked code parameters still do not initialize login or grant any access.

An explicit login click previously waited without a bound if SDK init stalled.
Its 15-second wait now ends with a retryable message while sharing the unresolved
SDK init. The timed-out click cannot later call provider login; only a fresh
click can continue once the SDK becomes ready. Callback startup still preserves
the URL and uses the existing recovery screen instead of rendering over init.

Public LIFF configuration was checked without cookies/tokens: the existing
endpoint covers Atlas and includes openid/profile. A downloaded official SDK
2.31.2 completed init and generated the expected PKCE authorization request in
an isolated browser using static public fixtures; all provider navigation was
blocked before leaving the fixture. An anonymous read-only authorization request
accepted the Atlas return path. Supabase aggregate logs showed normal user-settings
responses, with no account/token contents queried. No provider configuration,
production account, grant, database, function or deployment was changed.

Seven unit regressions failed on the prior implementation (six handoff shapes
and the stalled click). Browser coverage checks URL ordering, explicit retry,
late completion, server-grant validation and existing guest/account isolation
on desktop, mobile Chromium and iPhone WebKit. Actual results are in the PR.
Primary review is recorded here; independent signoff and successful real-account
consent / physical iPhone return-to-PWA verification are not claimed.

## Current follow-up: free self-use PWA (2026-10-26)

The existing manifest already supported standalone display; installation was
undiscoverable, ordinary browser sessions could never authenticate, and an open
installed app had no deployment-version check. Settings now provide iPhone/iPad
Safari instructions, LINE-browser escape/copy guidance, explicit Android native
prompts with honest cancellation/completion state, and public build-version
checks. Safe-area-aware dialog bounds support portrait and landscape. Existing
manifest identity, scope, icons and the normal GitHub Pages deployment remain.

Browser/PWA LINE authentication is explicit. Guests neither load LIFF nor
persist application actions. Recognized callbacks await SDK initialization
before routing; a bounded recovery screen preserves the callback if it stalls.
Only the existing server-verified access policy enables device/cloud stores.
Signout invalidates retained token callbacks and isolates the workspace without
deleting saved account drawings; inside LINE it does not call unsupported SDK
logout. Fresh external launches require tapping login to recover the SDK
session and reverify permissions. The existing public access endpoint rejected
an invalid test token with HTTP 401; no production account or grant was changed.

Version metadata and HTML carry the same deterministic build fingerprint.
Checks use no credentials or app storage, reject other scopes/invalid responses,
and never automatically reload over drawing work. Explicit reload retains the
route and discloses that guest work is temporary. The screener keeps its worker
retirement and no-background-cache policy; current installability does not
require a worker. No offline-launch/new-market-data or background-alert promise
is made. There are no new paid services, packages or native toolchains.

Primary review accepted fixes for safe-area scrolling, non-blocking LINE timeout
recovery, unsupported inside-LINE logout and compact login explanation layout.
Read-only review found that LINE's user agent would restart a stalled login
after the guest-recovery link. An explicit RAM-only guest URL now suppresses
identity initialization and refresh until a new login click; it cannot grant
storage, and is removed from subsequent OAuth redirects.
The first full PR run found that the new 44px account actions reduced the
landscape chart below its existing minimum. Short landscape chart headers now
use smaller outer spacing, preserving the account touch targets and the
unchanged chart geometry assertions.
Tests cover platform instructions, native prompt outcomes, standalone detection,
icons, update/reload errors, guest zero-persistence, denial/mismatched grants,
callback ordering/cancellation/timeouts, token expiry/signout and restoring
saved drawings after a fresh launch and explicit reauthorization. Actual totals
and exact-head CI results are recorded in the PR. The runbook documents real
device installation and LINE-provider prerequisites; mocked WebKit is not a
physical iPhone installation or successful real provider login. No external
independent signoff, merge or deployment is claimed.

## Current follow-up: market charts, navigation recovery and concise analysis (2026-10-25)

Market index previews now use the report's configured SMAs at 2px, retaining
full-history warmup, and display an independent volume scale. Native pan/pinch
and explicit zoom/reset controls operate inside the card; dedicated links open
the full workspace. Passive stock previews and individual-stock indicator
defaults remain unchanged. Interactive market charts retain attribution.

The reported navigation blackout was not reproduced in a fresh desktop load of
the exact published assets fetched with TLS verification. A reproducible missing
lazy-bundle case is now handled by route boundaries: navigation stays mounted,
an actionable fallback replaces the failed page, and explicit reload retries
the current deployment. Hidden workspace failures remain inside the hidden host.
A real MarketDataProvider/MarketTiming import cycle was removed using leaf
timeframe metadata, preserving public exports. Production builds now reject
cyclic cross-chunk reexports; an in-memory restoration of the old cycle fails
that gate. This is an eliminated risk, not proof of the user's precise cause.

Commodities include genuine Binance spot BTC/USDT, with USDT quote currency,
BTC base volume and completed UTC daily sessions, including weekends. Its
permanent record and hash/pair-validated dated supplement are reviewed source
records; existing published pair snapshots remain authoritative. No BTC/USD
substitution, browser exchange dependency or source-archive rewrite is used.

Volume analysis presents directional ratios first, then centered anomaly
controls. The two volatility values are larger independent cards. Shared
explanations use compact collapsed controls; per-stat help sits beside the
metric title, and accounting notes follow the financial charts. Observation
dates, stale/unavailable states, errors and meaningful empty states remain
visible. Root accepted and corrected attribution, hidden-boundary containment
and explicit BTC unavailability handling during implementation review. Read-only
agent review also identified a malformed Binance row escaping the retention
path; row schema validation now converts it into a source failure, preserving
the existing CSV and candle snapshot. A regression replays the malformed row.

Focused browser regressions cover configured MA/volume painting, native chart
interaction, repeated navigation, missing chunks/reload, BTC source boundaries,
analysis order/centering, large volatility cards and note expansion by keyboard.
Actual test totals and exact-head Actions results are recorded in the PR.
This package records primary review and implementation-agent contributions;
it does not claim independent sign-off or physical-device verification. No
merge or deployment was performed.

## Current follow-up: retain valid market data during closures (2026-10-25)

The deployed Sunday report had an empty Taiwan full-market scan and zero-count
breadth. The daily CSVs had acquired market-wide zero-volume placeholders dated
2026-10-23, while real trading ended on 2026-10-22. Scanning applied its unchanged
volume floor to those placeholders; breadth selected their modal date; filter
metrics also consumed the extra bar. Full-universe data itself remained intact.

A shared read-only session projection now excludes weekends and broad,
known-zero market dates before scan, metrics, custom-group and breadth analysis.
Weekday inference requires independent histories and adequate recent coverage;
any positive or unknown volume prevents that inference. A halted individual
stock on an active market date is not rewound. Taiwan and US cohorts remain
separate. Raw caches, permanent archives, report volume thresholds, same-date
filter guards and public-owner permission checks retain their existing behavior.
Partial breadth refreshes preserve the other market's prior valid snapshot.
Scan groups display their actual observation date alongside existing metadata.

The recovery CLI prepares separate review files only. It validates retained scan
prices/volumes against current raw histories, requires the latest genuine
session, keeps original metrics, computes the newly added complete-window
average volume, and rebuilds breadth. The actual replay restores all 92 saved
Taiwan candidates without rejection and breadth of 1,949 stocks (MA20:
494/1,947 = 25.4%; MA60: 720/1,945 = 37.0%), dated 2026-10-22. Other markets and
groups remain byte-equivalent as JSON values. Source hashes and recovery
provenance are in `docs/closed-market-recovery-proof.json`; the browser fixture
contains a documented three-row real excerpt. Generated repair artifacts belong
on a separate PR targeting `generated`, never in the source PR.

Primary self-review checked sparse/unknown-volume data, long closures, individual
halts, cross-market isolation, missing-source retention, mismatched saved rows,
input immutability and the early public-owner privacy guard. The existing CSV
cleaners converted missing volume to zero; their analysis views now retain the
unknown-volume lineage before the closure inference, with an actual-CSV reader
regression. New browser cases
cover Sunday and long-closure clocks, breadth, candidates, average-volume and
prior-session-volume filters on desktop, phone Chromium and iPhone WebKit.
Actual verification totals and exact-head Actions results are recorded in the
PR. No independent review, physical-device verification, merge or deployment is
claimed.

## Current follow-up: index directional volume above market entry rates (2026-10-25)

Taiwan and US market pages now show an index-volume card directly above full-market entry success rates. The card defaults to the market's first configured index, offers every configured index in that market, and exposes the existing 5/10/20-session directional ratios immediately. It contains no abnormal-volume calculation or RVOL thresholds. Stocks retain their abnormal-volume controls and share the unchanged directional-ratio calculation and presentation.

The analysis and overview candles share one daily request, including its source/as-of/cache metadata. Calculations use each selected index's own completed daily bars, capped at both the current time and the response's original observation time so an old pre-close response cannot acquire a completed session later. Zero volume, invalid OHLC and unverified jumps over 40% break adjacent comparisons; flat prices and equal volumes retain the existing denominator rules. Missing directions are N/A. Index changes clear prior values while loading, failed requests can be retried, and source descriptions remain folded.

Regressions cover exact directional numerators/denominators, window boundaries, zero/invalid data, Taiwan and DST-sensitive US closes, stale pre-close responses, shared-request deduplication, retry recovery, correct card order, index/market isolation and three browser profiles. The primary engineer performs self-review; independent or physical-device sign-off is not claimed. Final validation belongs in the PR, which must remain unmerged and undeployed.

## Current follow-up: compact normal chart on phones (2026-10-25)

The valuation explanation is now a floating info control immediately to the right of the P/E values. K線, 財務, 波動率, 價量 and 建議均線 share one compact row; the volatility menu retains both existing calculations and their explanations. The chart's volume menu mounts the analysis directly, while financial-page disclosures retain their existing expandable presentation.

The primary engineer accepted the first CI finding that compact chart tabs must retain the existing 36px minimum touch target. Their outer padding is removed on phones, preserving the compact row height and the original financial-navigation assertion.

Chart-route layout allocates the remaining height after the account notice and any legacy-import content. The normal chart extends to the top of site navigation; market-source details are available from an info control beside refresh instead of reserving footer rows. Actionable legacy import prompts retain their readable minimum chart area. The engine and calculation/storage boundaries are unchanged, with resizing handled by the existing observer and public Lightweight Charts APIs.

Browser coverage measures button alignment and P/E help placement, verifies the real chart canvas resizes with its host, checks the normal chart's bottom edge against navigation, and exercises 320/375/390px portrait, landscape, resized viewports and safe insets. Opening either explanation must leave the plot height unchanged; opening 價量 must show results without another disclosure click. Existing stock switching, MA toggles, drawing and fullscreen regressions are included in validation. This follow-up uses primary-engineer self-review; no independent or physical-device sign-off is claimed. Final checks are recorded in the PR, which remains unmerged and undeployed.

## Current follow-up: visible monthly history and reversible MA choices (2026-10-25)

The existing generated report still had weekly cohorts and no monthly points. Full-market output and old full-market parsing now omit the weekly cohort. A real historical replay is committed in `docs/report/breakouts-backfill.json`, with its source and calculation proof beside it. The latest-report loader lazily bundles that record and supplements a missing history only when the market date and all parsed calculation parameters match. Live latest statistics, explicit newer histories (including gaps), and original saved reports remain authoritative. The recalculation time is disclosed in the existing collapsed explanation.

The replay validated 3,480,830 archived OHLC rows at archive commit `4c934296ac3bb1003d0d4a7f106d21a099cddb87`, restored 7,328 rolling histories, and fetched the complete official Taiwan and Nasdaq lists. It physically clipped Taiwan at 2026-10-22 and US at 2026-10-23 to match the existing report, then recalculated April through September plus the reference date with the unchanged engine and coverage gates. All fourteen market/date points pass 90% coverage: Taiwan 98.13–99.29%, US 96.07–98.60%. Each method has complete samples for all four outcomes. A separate input comparison confirmed that applying the existing main correction journal produces exactly the same 7,328 input frames; its sole quarantined session is already absent from the archive inputs. No source archive or production report was written.

Volume analysis is an independent chart-header control alongside ATR20%, average amplitude and recommendations. Official disclosures remain last and subdued inside recommendations. Candidate MAs now toggle on/off, preserving locked instances and other stocks/timeframes. Mobile analysis controls occupy their own row so all four controls remain reachable without covering the chart/financial tabs or bottom navigation.

Primary engineer accepted and fixed two review findings: an incompatible signal definition must never acquire the saved history (full parameter and box-threshold checks), and mobile controls must not overlap the stock tabs. Browser geometry assertions now reflect the accepted independent analysis row while retaining overlap and overflow gates. Source proof, immutable-input checks, shared-load/refresh/error handling, archive isolation, actual bundled historical values, individual MA persistence, and three browser profiles have regressions. Final checks are recorded in the pull request; no external sign-off, merge or deployment is claimed.

## PR 171 chart and research follow-up (2026-10-24)

This follow-up makes all moving-average candidates individually applicable, defaults newly added chart MAs to fixed 2px widths, and seeds fixed-stock editing from applied daily SMAs only. Existing explicit styles and stored preset data remain compatible. The unused preset UI is removed because indicators already belong to each stock/timeframe; the chart-local shortcut list is now labelled 圖表收藏, with its purpose explained separately from the condition-tracking console lists.

The two daily volatility values are independent controls next to 建議均線. Its floating menu contains the MA candidates, then volume analysis, and subdued official disclosures last; panels stay within the available viewport above navigation. Directional volume ratios compare actual adjacent session volumes while abnormal-volume RVOL20 remains unchanged. The backtest UI uses one SMA/EMA with independent upper-entry and lower-exit percentages, closed-bar crossings and unchanged next-open execution.

Full-market reports recalculate six prior monthly reference dates from cached bars, truncating the input at every date and retaining listing/coverage/sample rules. The UI replaces the near-week switch with monthly histograms, dated current values and a default five-month view. Missing historical coverage is a gap; older generated reports explicitly await the new monthly payload. Current listings imply survivorship bias, disclosed in the calculation note. Historical raw archives remain immutable. Financial column charts expose time zoom, pan and reset while preserving original values, gaps and YoY alignment.

Read-only peer findings returned to the primary engineer and explicitly accepted: support long Taiwan holiday closures when selecting prior reference sessions; include displayed volatility percentages in accessible button names; dismiss a sibling popup during keyboard focus navigation. Each fix has a regression. Verification results and the exact-head Actions run are recorded in the PR. No merge, deployment or production data collection is authorized by this change.

PR 169 had already been merged before this follow-up, so these changes are reviewed separately in PR 171. The broader drawing regressions also refresh two stale expectations from the existing readable-axis/gesture-status design: axis width follows the configured font size and still reserves most of the plot, and picker geometry compares the same active-tool state before and after opening.

The first exact-head CI run exposed moving world candle CSVs paired with frozen browser-fixture metadata. The fixture reader now clips retained real bars at the frozen data date and checks its terminal date/close; the OHLC assertion is unchanged. Real full-market monthly generation completed successfully in 13m31s (TW 1,945/1,981 and US 5,273/5,348 valid current-session histories), but the following research checks exhausted the old 15-minute job budget. That job now allows 30 minutes for the expanded workload with all coverage and result checks intact. Touched calendar filters use pandas' equivalent `day_of_week` property to avoid deprecated-alias warning spam.

## PR 169 follow-up: Pages disclosures and collector cache (2026-10-24)

The unmocked Pages smoke now expands the SEC 8-K coverage note before its existing visible `CIK 對應` assertion. The other smoke targets remain visible outside the collapsed provenance, stat notes and chart legends; their assertions are unchanged.

The optional daily watchlist job restores the newest `universe-full-v1-` daily-bar cache through `actions/cache/restore` only. The collector prefers `data/<symbol>.csv`, then reads the cache's `bars/<symbol>.csv` only when the primary file is absent, with the existing CSV parser and report-session alignment. Missing fixed references still preserve observations and now name the affected symbols. Filesystem-backed tests cover cache-only Taiwan and US stocks, excluding newer sessions, primary-source priority and both sources missing.

Local validation: 1,151 unit tests, lint, normal/Pages builds, 10 disposable PostgreSQL permission tests and all 10 offline integration suites (238 tests) passed. Browser and final CI outcomes are recorded in the PR. No production collector, deployment or merge is invoked.

## Atlas interface refinements (2026-10-24)

The requested twelve changes compact financials, remove automatic chart MAs, expose archived entry-success calculations, use 20-session average share volume with Taiwan lot display, align fixed-MA filtering across saved-list evaluations, and reduce permanent explanatory text. [Scope, review triage and data boundaries](docs/UI_REFINEMENTS_2026-10-24.md) document the final behavior. Review findings were returned to the primary engineer and explicitly accepted before implementation. Final verification is recorded in the pull request; no external review, deployment, merge, or physical Safari sign-off is claimed.

## Current review: account storage authorization (2026-10-23)

Guests and ungranted LINE users can use chart drawing, groups, stock MAs and lists in memory without app-managed persistent writes. The verified owner alone can choose none/device/cloud grants; owner identity is enforced by the server. Device mode scopes all personal storage and disposable caches by account and mode. Account/policy changes replace the workspace and cancel stale operations. No shared legacy record is automatically adopted, migrated, deleted or uploaded. Both Edge Functions and SQL enforce cloud permission, and scheduled consumers reject ungranted private operations and other-account public artifacts. The bounded policy table is changed only by explicit owner actions, never by login. A device-mode owner can still administer grants. Mode-specific notices and non-cloud list renaming preserve usable guest/device behavior.

The PR also provides an explicit one-click legacy import, so upgrading requires no manual export backup. After server verification, device/cloud users can inspect source counts on the console or chart page and choose Import or Not now. Legacy IndexedDB is read without an upgrade; known unscoped localStorage settings are validated separately. Import merges missing items into the current account/mode and keeps the account version on conflicts. Source records remain untouched for retries or a second account, while per-account/browser import or dismissal markers suppress repeated prompts. The console storage notice can show the prompt again. Guests/none never inspect or import legacy data; cloud copies use the existing server-checked sync. Authentication, cache and old sync-binding metadata are excluded. An account load/validation failure blocks legacy merging rather than treating unread account records as absent.

Legacy-import verification: clean `npm ci`, full Vitest 96 files / 1,133 tests passed; the subsequent failed-account-load guard and coordinator/sync regressions passed 43 focused tests. Typecheck, lint, normal and screener Pages builds passed. The legacy import and account privacy browser selection passed 33 tests, plus 3 additional server-only cloud drawing cases: 36 passed, zero failed, across desktop Chromium, iPhone-size Chromium and iPhone WebKit. Tests cover v1–v4 read-only source migration, no automatic import, guest/none source-read denial, current-account conflicts, repeat imports, another account on the same browser, markers/reopening, grant changes and in-flight cancellation. Phone prompt screenshots were inspected for readable counts/buttons and chart visibility; this is not a physical-device sign-off. Final exact-head CI results are recorded in the PR.

Implementation boundaries: [ADR-016](docs/architecture/ADR-016-account-storage-authorization.md); [deployment, retention and management instructions](../docs/ACCOUNT_STORAGE_PERMISSIONS.md). Production SQL, both functions and service deployment have not been executed. Do not merge or claim the public website is updated. Device data cannot be remotely erased; no claim is made about browser-owned history/HTTP cache or LINE infrastructure logs.

Verification checkpoint after integrating main: 1,062 Vitest tests passed, including actual Edge handlers, account access/expiry and persistent storage separation; the three added worker-retirement tests also passed. The full Python CI suite passed 1,601 tests. Disposable PostgreSQL 17 integration passed 10/10, including revoke/write serialization and direct-table privileges. The initial focused desktop Chromium, iPhone-size Chromium and iPhone WebKit run passed 27/27. Typecheck, lint, normal and screener Pages builds passed. Final exact-head CI and complete browser results are recorded in the PR. No independent-model review or physical-device sign-off is claimed.

The broad browser run exposed a pre-existing race: a hidden lazy panel assertion succeeded before its chart header mounted, then its synchronous size measurement read a null element. The test now explicitly waits for the header and valuation before retaining the same size assertions. Cloud-chart mocks also intercept drawing requests so test tokens never reach production. A blocked CacheStorage/unregistration cleanup now allows the RAM workspace to render; unit coverage checks both denied cleanup and preservation of unrelated app caches.

The full site sweep also reached a previously unselected heatmap long-press test that called Chromium-only CDP in WebKit. Chromium retains its native touch dispatch; WebKit now exercises the DOM touch-pointer sequence and release-click suppression with the same tooltip, URL and no-dialog assertions. This is not a physical WebKit touch-hold verification. Heatmap regressions are now included in the PR workflow rather than skipped.

Earlier validation corrected an existing world candle/CSV mismatch using all 11 actual sources dated 2026-10-22. The subsequent main generated-branch cutover now owns those regenerated snapshots: this branch follows main and does not reintroduce removed generated copies. Retained world CSV records remain on main; current generated snapshots and offline fixture readers are validated by CI. No K-line history was removed by the legacy import.

## Integrated main: generated-branch PR 2 (2026-10-23)

Generated report copies leave main while source, retained records, old LINE PNGs
and history remain. Unit and browser research consumers now use frozen source
excerpts and retained world CSVs; CI additionally validates restored live inputs.
See [scope, provenance and Pages byte proof](../operations/generated-branch-pr2.md).
No UI, chart engine or generator behavior changes. Local/CI results are recorded
in the PR; no independent review or physical-device Safari sign-off is claimed.

## Previous review: compact chart header (2026-10-23)

The volatility row, its explanation, and the recommended-MA row and summary added five rows under the P/E and shrank the phone chart to about 260 px. `ui/StockStatsMenu.tsx` replaces them with one "波動・均線" button (beside the P/E on desktop; on the existing K線／財務 row on phones via a new `stats` grid area) whose panel floats over the chart and closes on Escape or an outside tap (dialogs opened from it do not close it). Both components stay mounted while closed, so data loads as before. Measured with the site e2e mocks: phone chart 260 → 353 px, desktop 553 → 616 px. `MaRecommendation` no longer has its own disclosure and now shows each pick's structural score and every candidate's score, with a note that the starred pick is chosen by the reversal-return gate and is not always the highest score.

Local verification: typecheck, lint, production and Pages builds passed; Vitest 83 files / 980 tests passed. Playwright desktop and phone Chromium for the full `atlas-review.yml` site selection: 149 passed, 1 pre-existing skip. Specs were updated for the new interaction (open the menu first; close it before tapping the toolbar it covers on phones) and assert the button shares an existing header row with no horizontal overflow; no assertion was removed. WebKit runs in CI only. No physical-device or independent review sign-off is claimed.

## Current review: ma-selector recommended moving averages on the chart (2026-10-22)

The chart header shows ma-selector's weekly recommended SMA for the symbol (short 15–33, medium 34–45, long 46–110) from `report/ma-selector/<SYMBOL>.json`, written by the new `ma-recommendations.yml` workflow from the public ma-selector repository (its production selector, unchanged; companion branch `claude/atlas-integration` there adds the batch script, `?ticker=` prefill and a link back to this chart). "套用到圖表" adds only the missing picks as daily SMA indicator instances (by id, never MA1/MA2) and switches to 1D; existing indicators, locks and other timeframes are untouched. "固定為個股參數" reuses the console's `FixedStockParameters` dialog (new `prefill` option keeps the picks in the input and shows the saved values separately) and saves `ungrouped` through the existing LINE-verified `stocks.save`. Missing files, SPA fallbacks and HTTP/network errors show a link to compute the symbol live in Streamlit instead; no values are invented.

Local verification: typecheck, lint and production build passed; Vitest 79 files / 945 tests passed (5 new). Playwright desktop and phone Chromium: the new `ma-recommendation.spec.ts` (apply, timeframe switch, fixed-parameter save payload, missing state, no horizontal overflow) plus stock-volatility, parameter-console, unified-console and site specs passed (52 cases). WebKit was not run locally; the spec is added to the `atlas-review.yml` selection. The weekly workflow has not run yet, so no live recommendation file exists until it is dispatched after the ma-selector branch merges. No physical-device or independent review sign-off is claimed.

Review scope: `src/report/maRecommendation.ts`, `src/ui/MaRecommendation.tsx`, `src/ui/FixedStockParameters.tsx`, `src/app/App.tsx` (one line), `src/ui/workspace.css`, `tests/ma-recommendation.test.ts`, `e2e-site/ma-recommendation.spec.ts`, `../.github/workflows/ma-recommendations.yml`.

## Current integration review: PR #148 with daily volume (2026-10-21)

Merged main `6b348b69758a330404b8bc73e03909d41c6803aa` into the composition branch with a merge commit, retaining PR #149's optional daily share-volume filter and all repeated quarterly/annual EPS and revenue conditions. `vol:N` is serialized alongside every fundamental token, evaluated inclusively with AND, and preserved by URL sharing, separate TW/US memory, local saved strategies and cloud validation. Draft volume-chip removal clears its custom input; Reset clears both volume and growth editor state.

CI run 39563277621 / job 119099356068 failed the same short-viewport Apply regression on desktop Chromium, phone Chromium and iPhone WebKit (152 passed, 3 failed, one pre-existing skip). The failure was reproduced locally: the non-shrinking navigation and selected-chip area exhausted the 280px viewport and pushed the footer below it. The new flex content wrapper lets navigation, chips and conditions share scrolling when the visible viewport is short, while the header/footer remain fixed. Short mode follows visualViewport height, including keyboards that do not resize the layout viewport. No existing test was removed, skipped or weakened.

Added combined-filter regressions cover an exact 1,000,000-share boundary, below-threshold and missing volume, each independently failing annual EPS gate, missing-data policy, independent chip removal, route round trips, fresh-context shared links, separate market memory, saved-list reload/evaluation, reset, and cloud strategies longer than 300 characters. All four repeated quarterly/annual EPS/revenue gate types are checked with volume. Browser coverage also asserts the complete Apply button fits a 280px viewport with stacked conditions and simulates a keyboard shrinking only visualViewport.

Local verification: 940 unit tests passed; lint, production and Pages builds passed; all five CI offline Python integration suites passed (9 + 38 + 6 + 22 + 18 cases). All nine final focused browser cases passed: combined filters, the unchanged short-viewport regression, and the visualViewport-only keyboard case, each on desktop Chromium, phone Chromium and iPhone WebKit. WebKit was downloaded successfully through its official fallback URL; its missing system libraries were extracted under /tmp and its downloaded launcher preserves LD_LIBRARY_PATH. These are local environment setup only, with no CI or browser-test exclusions. The full CI browser selection and fundamental regressions are also checked separately. No physical Safari or independent reviewer sign-off is claimed.

## Original composition review

The filter drawer uses orange accents, category navigation, removable draft chips and growth-condition cards. EPS and revenue gates can each have multiple quarterly or annual instances, combined with AND. Repeated `eps`, `epsy`, `rev`, and `revy` URL tokens preserve these instances. Removing the first instance promotes the next instance of that metric; saved single-condition strategies remain compatible. Malformed growth tokens are ignored without erasing valid conditions.

`acc:1` retains quarterly EPS acceleration; `accy:1` selects annual acceleration. Both tokens require both periods to accelerate. Each compares the latest two consecutive YoY slots for its period, with the existing turnaround and missing-data policy and no cross-period fallback. Row detail tooltips include every stacked gate. The existing EPS streak is kept in a collapsed advanced section: unlike an annual gate, it requires YoY strictly above zero and stops at missing years or flat/declining EPS.

Filter limits increase from 300 to 2,000 characters consistently in routes, local/saved-list validation and the user-settings Edge Function. Existing persisted strings require no migration. Cloud support for longer strategies requires deploying the changed Edge Function. No deployment is claimed.

Original PR verification before integration: clean `npm ci`, full unit suite (928 passed), production build and lint/typecheck passed. All 38 desktop/mobile Chromium filter cases passed, including no horizontal overflow, annual-vs-quarterly acceleration, stacked EPS/revenue rules, preview counts, reload, per-instance removal, draft cancellation and reset. After the final extra-gate input correction (clearing a threshold keeps its card available for editing), all 8 focused desktop/mobile regressions passed and lint/production build passed again. WebKit could not be installed because the managed outbound policy rejects both official download hosts with HTTP 403 (Domain forbidden). No Safari or independent review sign-off is claimed.

Review scope: `src/pages/screenerFilters.ts`, `src/pages/screenerItems.ts`, `src/pages/ScreenerPage.tsx`, `src/ui/FilterSheet.tsx`, `src/ui/filter-sheet.css`, `src/app/routes.ts`, `src/watchlists/model.ts`, `../supabase/functions/user-settings/watchlists.ts`, and the screener unit/browser regressions. Confirm AND behavior, independent annual/quarterly acceleration, per-instance removal, URL and saved-strategy restoration, draft cancellation/reset, missing data, turnaround handling and phone overflow.

## Integrated review: optional daily share-volume threshold (2026-10-21)

Daily collection does not filter the Taiwan ticker list by traded shares, so low-volume and zero-volume CSVs continue updating. After all CSV downloads/updates, the daily LINE report's Taiwan `tw_all` MA20 list requires the latest bar's volume to be at least `TW_MIN_VOLUME = 1_000_000` shares. This report-only threshold applies to both `.TW` and `.TWO`; the US S&P 500 MA20 list has no volume threshold. The full-market screener data remains unrestricted by volume, and its optional page filter is unchanged.

The screener's 「每日成交股數」 condition defaults to unrestricted. Presets and a custom non-negative integer input use shares in both markets (1 Taiwan lot = 1,000 shares). A positive threshold is inclusive; empty/zero clears it. The `vol:N` URL token uses the existing per-market filter storage, chip removal, reset and sharing paths. Missing share counts are excluded when an available threshold runs; groups without share data show the existing unavailable-condition treatment. Invalid custom inputs block Apply.

Report-only follow-up regression coverage confirms new and existing low-volume listed/OTC stocks are downloaded and written to CSV while excluded from `tw_all`; exactly 1,000,000 shares qualifies, earlier bars do not determine the threshold, and low/zero-volume US stocks still match the MA20 report. README and universe metadata distinguish unrestricted collection from the Taiwan LINE report's minimum. Full Python verification: `python -m unittest discover -s tests -v`, 1,354 tests passed in 134.863 seconds. The screener-page source, frontend tests and browser tests were not changed by this report-only follow-up.

Verification: `npm ci` from the existing lockfile/cache; 920 Vitest tests passed against latest main; lint and TypeScript checks passed; normal and Pages builds passed (`VITE_PUBLIC_BASE=/my-stock-screener/atlas/`). Python collection/metrics/full-universe regressions: 88 passed. Final browser cases: 6 desktop/mobile Chromium plus 3 iPhone WebKit passed. Reviewed screenshots in `.cache/volume-filter-screenshots/` confirm the custom input and preset layout fit the mobile sheet. Earlier browser attempts exposed incorrect test expectations for matched/unmatched counts and filtered totals; corrected fixtures/assertions were rerun. WebKit required locally extracted libraries and a downloaded launcher adjustment to preserve `LD_LIBRARY_PATH`; these are environment setup only. Browser tests use synthetic data and do not establish physical-device behavior or live full-market refresh performance.

The changes are based on main `674bdaa77`. The original audit recovered 23,211 bars in the older workspace; after retaining newer main data, this PR inserts 16,606 bars into 630 CSVs, preserves all existing main rows, and does not recreate 31 files removed by main. The report records both the prior audit summary and the integration baseline. Backfill and OHLC cleanup regressions add 6 passed Python cases to the 88 collection/metrics/universe cases above.

Independent review is unavailable; no independent sign-off is claimed. The public site will receive the selector when the normal publishing workflow runs.

## Previous review: three entries and four success rules (2026-10-21)

User authorized updating PR #147, including the earlier ATR20%/mean-intraday-range feature, the reviewed substantial swing definition, three entry methods, four success rules and a styled comparison panel. Do not merge or enable auto-merge.

Swing waves require a close rebound of 2 × Wilder ATR20 from a trough, followed by a close reversal of 2 × the extreme-date ATR from the highest wick. Distances are frozen at extrema and confirmations become usable next bar. Sideways micro-pivots do not replace a reference. A strict intraday break is recorded as an attempt. A close below key + 0.1 × Wilder ATR14 fails all four outcomes but keeps the same wave high armed; later days may retry. A qualified close consumes that high. Frozen real-data regressions include MU's failed September 21 / retried September 22 entry, 3230, 3287, VEEV and 2305. No complete historical Livermore/1-2-3 system is claimed.

All methods share one five-complete-market-session cohort (D+1 through D+5). Rule 1 survives key −5%; rule 2 exceeds the signal-day high on D+1; rule 3 resolves the FIRST stop/target/next-day-high event, or succeeds after five-day survival; rule 4 requires target +5% AND next-day higher high, without an added −5% veto. Signal-day close failure overrides all. Opening gaps establish known order; otherwise opposing events on the same daily candle conservatively lose. Resolved first-event outcomes never change with later price action. Pending/missing observations never become failures. Legacy as-of-profit fields are retained for compatibility and never shown as the four new rates.

The market panel has a four-choice outcome selector, three method cards, exact numerator/cohort counts, pending/missing counts, and a collapsible 3×4 comparison matrix. It supports keyboard/native button interactions, phone targets, horizontal overflow containment, empty/pending/partial/legacy/error states and a short shared-failure explanation with detailed methodology. Old report versions wait for recomputation rather than relabelling earlier rates. Preview images use the production pivot trace and explicitly shade missing stock candles; guessed fixed-offset pivot dates are not used.

The full-market builder still restores complete exchange lists/histories, never shrinks its denominator to local CSVs and preserves the previous file if coverage is insufficient. PR CI recomputes a real artifact read-only; the resulting verified JSON is committed separately. Local subset trials are never published as full-market results. No production account, database, cache or notification writes are made.

Local verification: full Python suite 1,395 passed, including 84 focused breakout checks (38 shared/legacy, 6 full-market, 22 wave, 18 new outcome cases). Lint, typecheck and both builds passed; 20 desktop/phone Chromium breakout and volatility cases passed. All 927 frontend tests passed on the final full run; final CI results are recorded in the verification file and PR description. No independent reviewer or physical Safari sign-off is claimed.

Verified data: the real report from read-only run 39568611989 / job 119116912119 restores cache universe-full-v1-39433983829-1, with the artifact digest and full 3×4 results in the verification record. Both markets are dated 2026-10-19, with TW 1,948/1,979 and US 5,282/5,347 valid/listed stocks. Real-statistics desktop/phone renders match the artifact and have no runtime errors or horizontal overflow.

## Previous review: daily volatility and breakout entry methods (2026-10-20)

Individual-stock chart headers show Wilder ATR(20)% and the mean of the last 20 daily (high-low)/previous-close percentages below P/E. They request daily bars through the selected normalized provider independently of the chart interval, exclude unfinished sessions, require 21 valid bars and use the last daily close for ATR%. Symbol/provider/reload identity and cancellation prevent old values leaking into a new request. Expandable help shows formulas, the data date, cache status and price-basis limitations. No chart engine or drawing model changes.

Markets displays confirmed swing-high breakout, 20-day box breakout, and EMA20 upward crossing. Swing highs use a two-left/two-right local turn (rightmost equal high on plateaus), confirmed only after the right candles close and eligible from the following bar. The latest confirmed swing high persists until another pivot is confirmed; it never rolls off at an N-day boundary. A strict intraday break triggers even if the close falls back below the reference, without volume, a higher-low/full-123 requirement or a ten-day cooldown. Each pivot contributes only its first break; separate pivots can contribute multiple events in a window. The 20/60/120-day selector and rolling-high substitution are removed.

Swing stop-entry is approximated by max(open, pivot): a gap cannot fill at the lower old reference, and a failed intraday break cannot fill cheaply at that day's close. These are daily-bar research assumptions, not verified tick fills; no fees/slippage/minimum price increments are simulated. Box/EMA retain close entries, their existing cooldown and first-event policy. All rates require three trading days of maturity and mark to the report-date close. Full-market reports publish exactly these three methods; legacy N-day results remain in the internal engine tests only and are never relabelled as swing statistics.

All three methods now scan the full-market universe: Taiwan listed/OTC ordinary shares and US exchange ordinary shares/ADRs, using the existing universe-full listings and completed daily-history cache. Entry success is profitable mature signals divided by mature signals. The separate market up-rate and comparison are removed from the UI. Coverage reports the entire listing, loaded/missing histories, current valid bars and stale/non-trading stocks; per-method history eligibility is shown separately. No signal, unavailable history and unmatured signals are not failures. Legacy engine benchmark fields remain for compatibility but are not shown.

The full-market offline builder never shrinks the universe to available CSV names or falls back to S&P 500. It requires fresh complete exchange lists and at least 90% valid daily-bar coverage before atomically replacing the report; otherwise it preserves the prior file. Legacy partial-market reports are hidden. Production report generation moves to universe-full.yml after full-cache refresh, and the old limited-universe schedule step is removed. PR verification restores the default branch's existing cache read-only, builds the real full-market report and uploads evidence; it does not update the cache, production data or notifications.

See [calculation and verification record](docs/VOLATILITY_BREAKOUT_VERIFICATION.md). Independent review and physical Safari verification are not claimed. Submit for PR review only; do not merge or enable automatic merging.

## Historical review: parameter console follow-up (2026-10-20)

Selected watchlists now auto-save authenticated settings and delete cloud-first through a serialized per-list queue. Permanent stock MAs are dormant ungrouped stocks records; they survive list/member removal and only override an active member's defaults. The daily report and breakout consumers both exclude dormant rows. The Edge Function requires ATLAS_FIXED_PARAMETERS_ENABLED=1 after consumer deployment to avoid old monitor builds treating the new reserved group as active.

The parameter list restores search and group filters with an approximately five-row scroll area. Native edit/delete dialogs keep errors visible and provide confirm/cancel. Review [ADR-015](docs/architecture/ADR-015-fixed-stock-parameters.md) and the rollout in [UNIFIED_CONSOLE.md](docs/UNIFIED_CONSOLE.md). Actual offline test/CI results are recorded in the follow-up PR. Production deployment, real-account acceptance, physical Safari and independent review remain unverified.

## Historical review: unified console extension (2026-10-20)

The user explicitly authorized the unified parameter console, multiple saved screening/manual lists, last-opening membership comparisons, optional Supabase persistence and existing breakout monitoring, quarterly US statement presentation, peer comparison, rotation tracking, health disclosure and the reachable Apply action. See [implementation and deployment checklist](docs/UNIFIED_CONSOLE.md) and [ADR-014](docs/architecture/ADR-014-unified-console.md).

Primary review covers source completeness, tenant-scoped API access and optimistic concurrency, separation of daily observations from last-opening baselines, failed-data preservation, quarterly/annual navigation and source/null integrity. Browser regressions detected duplicate sibling React keys for the financial header/peer section when switching symbols; the peer key now has its own prefix. Tests also cover short viewports and the fixed Apply footer. New list storage does not replace existing chart IndexedDB. Independent review and physical Safari UAT are not claimed.

Supabase migration/service activation, true-account cross-device verification and real notifications remain pending. No live database writes or notifications were performed. This branch is for PR review and must not be merged automatically. The final verification record is maintained in UNIFIED_CONSOLE.md.

## Historical review: SEC bootstrap and Taiwan public verification (2026-10-08)

**Status: engineering, deployment, and public verification complete; physical-device UAT and independent review remain pending.** Commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) completed Actions run [37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) at 2026-10-08T13:36:11Z. TLS HTTP verification passed at 13:38:04.608649+00:00; public iPhone WebKit passed at 13:37:56.193Z. Evidence: [HTTP](docs/taiwan-financial-live-http.json), [browser](docs/taiwan-financial-live-browser.json). All 16 seeded SEC packs are publicly validated; NVO/QQQ/SPY/TSM remain unavailable after SEC 403/cooldown, with no fresh runner data claimed. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained market packs keep their original observation dates and are not current-quote claims. XOM's market-pack status is separate from its SEC seed (two quarterly, zero annual facts).

Primary accepted and closed LIVE-DOC-01/02: the older picker deployment is now identified as historical in README, and the latest hosted market-refresh counts and retained-stale limits are stated separately from the earlier local snapshot check and SEC seed statuses.

The versioned seed set under `data/financial-snapshots/` has 16 normalized packs: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, SMCI, NFLX, JPM, WMT, XOM, AVGO, PLTR, and MU. Four other allowlist entries may remain unavailable. XOM uses current CIK `0002115436` and has two quarterly, zero annual facts. Seed data is used only when cache is missing/invalid or has strictly older `fetchedAt`; all original timestamps, nulls, and audit metadata are preserved. Aged cache is marked stale, refresh is attempted after 24 hours, and failed refresh retains valid data. Seed-write errors are isolated by issuer. Local mocked-403 proof: 16 seed-backed `fresh`, NVO/QQQ/SPY/TSM unavailable, four SEC calls, seed timestamps unchanged. No User-Agent/IP rotation or proxy workaround is used.

Latest checks: 225 unit tests across 29 files, lint, typecheck, normal/Pages builds, targeted collector/provider unit suite 18, normal browser 126 passed / 10 original skips, and Pages browser 32/32 passed. Combined browser result is 158 passed / 10 original skips. Source review covered 269 files. HTTP validation passed every asset byte comparison, font license, and all 16 pack schemas/audits/sources/nulls/original timestamps. Public iPhone WebKit passed with zero API/runtime errors: TWD/native Taiwan weekly/monthly values; AAPL quarterly and annual Revenue audit UI; three timeframe-owned locked SMAs and a future horizontal drawing across persistence/reload/export v4. Raw Revenue and derived FCF provenance passed HTTP pack validation; WebKit did not assert FCF UI. Root inspected public screenshots; chart values including 2550/2600 and date labels appeared coherent. BOOT-01/02 are accepted and closed. Next: open the existing [Atlas public URL](https://wudn9922.github.io/lightweight-drawing-lab/) and try Financials and Taiwan flows; reopen if cached, but do not clear site data. Gemini/Claude review and physical-device UAT remain pending.

## Completed review: drawing picker and ATR stroke defaults (2026-10-08)

Status: **Implementation, local verification, deployment and remote browser verification complete; independent review and physical-device UAT pending.** Source commit [0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1](https://github.com/wudn9922/lightweight-drawing-lab/commit/0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1) was published non-force to `main` with 217 reviewed tree entries (45 changed files). [Actions run 37741318516](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37741318516) succeeded with build and deployment at 2026-10-08T07:08:24Z. TLS-verified HTTP asset/hash checks match the tested Pages build; see [HTTP evidence](docs/picker-atr-live-http.json). The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z; see [browser evidence](docs/picker-atr-live-browser.json).

Frozen implementation removes the left rail in favor of a 44×44px outside-plot toolbar launcher with an icon and “繪圖”, plus a categorized modal for all ten tools. At widths up to 1099px, the duplicate delayed-snapshot CTA is hidden and the provider selector remains visible. Existing Select/Pan, magnet, history, zoom, future-area and reset actions remain available. Price-axis minimum width is 40px. New drawings and SMA/EMA use Wilder ATR(14) × 0.02 in price space, converted through public price-scale coordinates; legacy/custom widths and locks remain fixed, pixel fallback is retained, and Volume remains pixel-based. IndexedDB/settings v4 carries optional `widthMode`; market snapshot schema stays v3. See [ADR-012](docs/architecture/ADR-012-drawing-picker-and-atr-strokes.md) and [the verification checkpoint](docs/DRAWING_PICKER_ATR_VERIFICATION.md).

Primary accepted and fixed P2-PICKER-01–05: pointer tracking cleanup, Volume 2px default restoration, owned modal/gesture Escape handling, bounded category scrolling in short landscape, and cancel-tool-before-fullscreen-exit ordering. Sol's read-only review confirmed all five closed. The price-domain interpretation is frozen by primary; no user clarification blocks implementation.

Current checks: clean `npm ci`, 178 unit tests across 23 files, lint, typecheck, normal and Pages builds PASS. Latest normal output is `index-BboRNSm3.js`, Pages output is `index-CgkbpnRO.js`, and CSS is `index-C_ebhjyC.css`. Targeted picker browsers passed 24/24 across all four profiles in 1.9 minutes. The first full 132-case run stopped on stale exact Width selectors after SMA24/EMA56 accessible names changed. The second full run was interrupted by a mobile UAT assertion expecting always-visible Zoom buttons from the removed rail; runner 79856 exited 130. Primary accepted a test-only branch that checks desktop's 44px external geometry and mobile's picker-scoped 44px controls outside chart DOM, dismissal, hidden-when-closed state and stable chart height. Numeric volume/legend/reload assertions and all skips are retained; no product regression or source change is claimed. Focused all-profile mobile-uat and v1-journey coverage passed 20 cases across four profiles in 3.0 minutes; Sol accepted the adapted tests without weakening coverage. Final normal browser gate: 122 passed / 10 original expected skips / 0 failed in 12.0 minutes (`/tmp/atlas-picker-browser-final2.log`, session 3541, exit 0). Final Pages browser gate: 20 passed / 0 failed in 1.7 minutes (`/tmp/atlas-picker-pages-browser-final.log`, session 24739, exit 0). Combined: 142 passed / 10 original expected skips / 0 failed across all four profiles. TLS-verified HTTP assets match the tested build. The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z: six categories/ten tools, ATR settings and IDB reload/export v4, SMCI/NFLX delayed quote and 1D/1W/1M alignment, locked MA and monthly-future drawing reload, symbol/timeframe ownership, zero API requests and runtime errors. Root visually reviewed fresh 44px mobile chart/picker screenshots and marked PASS. Evidence: [HTTP](docs/picker-atr-live-http.json) and [browser](docs/picker-atr-live-browser.json). Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending.

Next: the user opens the same public URL and tries the categorized Drawing Tools picker, ATR stroke settings/defaults, and compact mobile controls. Existing saved widths/settings are not forced to ATR; choose ATR in a drawing or SMA/EMA setting to try it on an existing item. Close old tabs and reopen if cached; do not clear site data. Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending. No V2 work is authorized.

## Completed review: current-period quote consistency correction (2026-10-08)

Status: **Current-price extension implementation and verification 100% complete.** Primary triage and Sol read-only review closed P1-MONTH-01, P1-SCOPE-02, P1-CURRENT-03, and P2-CURRENT-04. Corrected source commit [ee10f6743e92de8cb1eb9bf994ce26235eb3eac0](https://github.com/wudn9922/lightweight-drawing-lab/commit/ee10f6743e92de8cb1eb9bf994ce26235eb3eac0) was published without force to `main` (205 files); [Actions run 37711284860](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37711284860) succeeded and deployed it. TLS HTTP asset/hash verification passed; the iPhone WebKit profile passed at 2026-10-08T01:18:28.720Z. Records are [here](docs/timeframe-price-live-http.json) and [here](docs/timeframe-price-live-browser.json). Quote-to-current-bar checks and the listed app journeys passed. Physical-device UAT and independent Gemini/Claude review remain pending; automated WebKit is not physical Safari. Do not start V2.

P1-CURRENT-03: the previous live SMCI quote was $44.94 as of 2026-10-07 20:00 UTC, while native current 1M remained at $43.46 from 2026-10-06. The accepted source fix heals only a validated missing latest daily close from matching metadata, requires fresh daily date/close agreement, and rebuilds only current 1W/1M buckets from validated daily OHLCV. It preserves native history and records provenance. Schema v3 rejects latest 1D and current 1W/1M quote/date/bucket mismatches; failed period guards omit that interval, while a missing/inconsistent fresh daily result preserves the prior whole snapshot. Sol's read-only review closed this source finding.

P2-CURRENT-04: primary accepted and fixed the stale cached-daily race. A fresh native period response triggers a single 1D10y retry only when the helper identifies older cached daily metadata; the same timeout signal is retained, raw cache is replaced, and unrelated helper failures do not retry. The focused proxy middleware regression passes.

Local gates: clean `npm ci`, 167 tests across 20 files, lint, typecheck, normal and Pages builds all PASS. Normal browsers: 98 passed / 10 original expected skips / 0 failed (12.2 minutes). Pages browsers: 20 passed / 0 failed (2.1 minutes). Combined: 118 passed / 10 expected skips / 0 failed. All 20 local schema-v3 packs have all seven supported timeframes. Refreshed SMCI daily/1W/1M close is $44.94, with volumes 90,284,251 (1W) and 161,285,951 (1M); refreshed NFLX close is $69.70, with volumes 102,053,115 (1W) and 182,309,915 (1M). These are refresh observations, not fixed future prices. Normal and Pages entry assets are `index-DV6Ur9iz.js` and `index-B36DL1MA.js`; chart/storage chunks are unchanged. The first four Pages failures were test-only: an older filtered NFLX monthly fixture omitted October 1/2. The fixture builder now uses actual current daily/weekly/monthly captures and provenance; assertions were preserved and no product guard was loosened.

Live smoke at 2026-10-08T01:18:28.720Z loaded same-origin delayed Yahoo snapshots. SMCI quote $44.94 and NFLX $69.70, as of 2026-10-07 20:00 UTC, matched the latest 1D/1W/1M close. Current period OHLCV, including weekly/monthly volume sums, is recorded in the browser evidence. The smoke also passed timeframe-specific SMA and drawing isolation, locked MA reload, monthly-future drawing locked reload, and settings export v3, with zero API requests or runtime errors. HTTP asset hashes matched the tested production build. The private smoke harness was corrected to capture the persisted expected snapshot after locking and keep gestures inside the plot; these were harness-only adjustments, and application source did not change after the passing gates.

Next: user opens the existing [public site](https://wudn9922.github.io/lightweight-drawing-lab/), selects Yahoo delayed data for actual prices, and tries the intended flows. Close old tabs and reopen them if the prior app version is cached; do not clear site data. Physical iPhone/iPad UAT and independent external review remain pending. Do not start V2.

# Atlas V1 independent review package

## Static SEC snapshot and Taiwan market review details (2026-10-08)

**Status: deployed and publicly verified with partial coverage.** Earlier Actions run 37764054644 had SEC company-ticker 403/cooldown and AAPL 404; the current deployment serves the 16 versioned seed packs. The four unseeded symbols remain unavailable after SEC 403/cooldown, without a fresh runner-data claim. Independent Gemini/Claude review and physical iPhone/iPad UAT remain pending; this package is not external sign-off.

### Frozen decisions

- SEC collection reuses the current `FinancialNormalizer` and preserves its quarter derivation, EPS rules, nulls, and raw/derived provenance. Normalized snapshot schema v1 is separate from settings/export v4.
- Generated output and status manifest under `public/financial-data/` are Git-ignored; normalized public seed packs under `data/financial-snapshots/` are intentionally versioned. A valid seed is used only when cache is missing/invalid or seed `fetchedAt` is strictly newer; timestamps, nulls, and raw/derived audit metadata remain intact. Aged cache is marked stale, refresh attempted after 24 hours, and prior valid packs retained on failure. Browser cache is bounded/disposable and validated.
- Per-symbol SEC outcomes are explicit (`fresh`, `retained`, `unavailable`, `unsupported`). A partial failure keeps any valid prior pack and must not block price deployment. The client uses the fixed truthful project identity; no User-Agent/IP rotation or proxy workaround is allowed for the observed SEC 403.
- Financial collection filters the mixed market-symbol list to its 20 US-profile tickers; Taiwan symbols are excluded from SEC requests and manifest. Keep six Taiwan market snapshots: `2330.TW`, `0050.TW`, `2317.TW`, `2454.TW`, `6488.TWO`, `8069.TWO`. A US-profile ticker/ETF without supported SEC companyfacts stays explicitly unsupported; preserve previous valid US packs.
- Use a committed schema-validated official TWSE/TPEx directory fallback, TWD, `Asia/Taipei`, and a 09:00–13:30 local session. Do not invent holiday data, Taiwan SEC facts, or a TSM ADR alias. Resolve ambiguous bare codes only with an explicit suffix.
- Yahoo's actual exact-13:30 Taiwan terminal-auction sample is a normalized explicit `sessionCloseObservations` entry, not an ordinary interval or fabricated bar. Research includes only validated marks as `CLOSED INSTANT` at `asOf >= timestamp`. Taiwan intraday counts may include this additional observation; preserve US calendar/count behavior.
- Advance the market Actions cache profile namespace; preserve the existing hourly weekday schedule. Refresh of the official Taiwan directory is optional and must retain its committed fallback on error. Database/settings/export v4 remains unchanged.
- Freeze the price-axis font/width design: use `Atlas Narrow Axis`, a 70%-horizontal-scale derivative of Barlow Condensed Regular under SIL Open Font License 1.1 ([OFL.txt](src/assets/fonts/OFL.txt)). Configure minimum width 0, 11px, and `minMove: 0.01`; trim redundant trailing zeroes only. No abbreviation, cropped/hidden labels, coarser rounding, or 8px text. Long numbers may expand naturally. This chart font also affects time-axis labels, while app UI font choices remain unchanged.
- Primary accepted P1-AXIS-06 is closed: the corrected font scales advances and outlines (SHA-256 `5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`); Sol passed all 694 glyph geometries, the integrated axis check passed all four profiles, and fresh measurement showed at least 30.8% width reduction for ordinary samples. Root inspected local and public screenshots; axis labels including 2550/2600 and dates appeared coherent. The earlier overlapping-font evidence is superseded. Local normal/Pages gates and public HTTP/WebKit verification pass; physical-device UAT remains pending.

### Final local verification

The latest local verification is 225 unit tests across 29 files, lint/typecheck, normal/Pages builds, targeted collector/provider unit suite 18, normal browser 126 passed / 10 original expected skips, and Pages 32/32 passed (3.3 minutes). Combined browser result: 158 passed / 10 skips. Source review covered 269 files. Public HTTP passed all asset byte comparisons and validated all 16 SEC packs; public iPhone WebKit passed as detailed above. Mocked SEC-403 collection left 16 seeds `fresh`, NVO/QQQ/SPY/TSM unavailable, made four SEC calls, and preserved timestamps. An earlier local market snapshot check recorded 25 available and `0050.TW` lacking 1W/1M. The latest hosted 26-symbol refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Original observation dates remain attached to retained packs; no current quote is inferred. The Taiwan directory contains 2,251 entries; the other five selected Taiwan symbols have all seven timeframes. XOM's market status is separate from its SEC seed, which has two quarterly and zero annual facts. FIN-01/02, TW-03/04, AXIS-06, and BOOT-01/02 are closed. Physical-device UAT and independent external review remain pending.

### Review map and test plan

Review `docs/architecture/ADR-013-static-fundamentals-and-taiwan.md`, `docs/TAIWAN_FINANCIAL_SNAPSHOT_VERIFICATION.md`, `.github/workflows/pages.yml`, `.gitignore`, `scripts/refresh-fundamentals.ts`, `scripts/refresh-symbols.ts`, `src/fundamentals/FinancialSnapshotSchema.ts`, `FinancialSnapshotProvider.ts`, `MarketProfile.ts`, `SymbolCatalog.ts`, and their focused tests. Confirm the collector's symbol-level retention/error summary does not hide whole-run infrastructure failures; verify the cache namespace/key paths, 24-hour freshness, same-origin fetch path and stale/offline labels. Check no raw SEC response reaches the browser, no financial values are fabricated, and no financial/ticker cache is exported with user settings.

Verify Taiwan canonical suffixes and ambiguity handling, six-symbol static limit, session/date behavior, currency display, closed/future bar boundaries, explicit validated `sessionCloseObservations` at 13:30, `CLOSED INSTANT` research inclusion only for `asOf >= timestamp`, and unchanged US semantics/counts. Inspect official-directory fallback preservation under refresh failure. For the corrected font, confirm advance and outline scaling match, integrated preload and service-worker/offline behavior, five-second startup fallback, no glyph overlap, full price labels, trailing-zero-only trimming, 11px axis size, and no company UI font change. Geometry review (694 glyphs), corrected four-profile integrated axis check, fresh ordinary-sample width measurement, and public chart raster inspection have passed. The next user step is to open the existing public URL and try the Financials and Taiwan flows; reopen if cached, but do not clear site data. Physical-device UAT and independent external review remain pending.

## iPhone screenshot UAT correction — 2026-10-07

## iPhone screenshot UAT correction — 2026-10-07

Scope: red/green volume and fixed full-window MA20, visible numerical SMA/EMA values, passive compact external legend, taller mobile chart, external rail zoom, app-shell fullscreen/CSS fallback. No model/schema/drawing engine/provider changes. Review source: ChartEngine, IndicatorEngine, Volume, App, useChartFocus, styles, IndicatorPanel, indicators-v1 tests and mobile-uat browser regressions. Official vendored Lightweight Charts skills and installed5.2.1 typings were used.

Sol6.1High planned and reviewed read-only; Luna6Max implemented frozen chart and UI tasks. Primary triage accepted: (1) existing Volume period1 must not accidentally create MA1—use fixedMA20 and retain schema; (2) preserve any-explicit-Volume suppression of legacy volume regardless visibility/scope; (3) blank series titles after relocating names/values to legend; (4) focused desktop Manage must open a sheet within the fullscreen root; (5) failed native exit retains a usable Exit control; (6) keep44px external band with bounded horizontal numeric scrolling, no new20×16 interactive help; (7) ordinary input Escape remains unhandled when no chart/modal/focus action is owned. Each implemented by primary or after explicit primary decision. Two initial desktop test failures were hidden-panel selector ambiguity; selectors now select visible controls and retain all assertions. Full regression then exposed selection-driven mobile status24→48 layout movement. Primary restored reserved48px mobile status independently of selection; original editing gate passed all3mobile/tablet profiles, and the new focus test asserts stable plot height after selection/lock. Subsequent gate found taller layout moved the Export toast over Lock controls; primary docked mobile notices above the plot and made text click-through while Dismiss remains interactive. Pressed timeframe is centered in its own scroller with bounded resize RAF/cleanup. Read-only Sol re-review found no remaining blocker. The final focused export/lock and focus gate passed8/8 all4profiles; Final full104-case gate94PASS/10originalexpectedSKIP/0FAIL; Pages8PASS, combined102PASS. Unit138/18files, clean ci/lint/normal+PagesbuildPASS. Source1426afc9 deployed in successful Actions37615088219; actualHTTPS/newasset hash comparisons and remoteiPhoneWK numericalvalues/MA20/lockreload/export/focus/PWA/backend-unavailable checksPASS, zeroAPIs/errors, TLSverificationenabled. docs/MOBILE_UAT_VERIFICATION.md and mobile-uat-live-*.json hold evidence. PhysicaldeviceUAT and independentexternalreviewremainpending.

Chart/indicator and final UI read-only review found no remaining implementation blocker. Gemini/Claude external review remains pending; this Sol review is not independent external sign-off. Physical Safari drawing feel/fullscreen browser chrome/PWA still requires real-device UAT. Validation results are recorded in docs/MOBILE_UAT_VERIFICATION.md and PROJECT_STATE.md after complete regression and deployment.

Status: **External Review Pending**. Gemini and Claude connector/reviewer tools are unavailable in this Codex Cloud task. No independent sign-off is claimed. This package can be pasted into Claude Web with the referenced source files. Execution agents implemented frozen specifications; every substantive finding below returned to the primary reasoning engineer for triage before fixes. This is internal engineering review, not an independent Gemini/Claude approval.

## Product and scope
Existing accepted Phase1/2 Atlas Research Terminal in /workspace, extended continuously to V1. One React/TypeScript/Vite Web/PWA, pinned Lightweight Charts5.2.1. No proprietary TradingView source/assets/AdvancedCharts, paid dependency, broker, real-money execution or V2 scope. Demo simulated; Yahoo unofficial delayed prototype; actual financials SEC EDGAR. Physical iPhone/iPad UAT remains pending.

## Read first
README.md, AGENTS.md, PROJECT_STATE.md, docs/architecture/ADR-001 through ADR-009. Baseline verification is archived in docs/VERIFICATION.md and docs/PHASE2_VERIFICATION.md; current V1 evidence will be in docs/V1_VERIFICATION.md. Do not treat earlier Phase1/2 counts as final V1 evidence.

## Drawing review
Files: src/drawing/DrawingModel.ts, DrawingStateMachine.ts, DrawingController.ts, DrawingPrimitive.ts, DrawingRenderer.ts, HitTester.ts, Movement.ts, MagnetEngine.ts, DrawingHistory.ts, Loupe.ts; src/tools/*.ts; ChartTransform.ts/TimeMapper.ts.

Same pointer ownership/RAF/history/persistence system for all ten tools. Trend/ray/rectangle/Fib/measurements have two Press→Drag→Release anchors, horizontal/vertical one, channel three. Anchors canonical timestamp/price; logical advisory across timeframe changes, pixels transient. Whitespace future area never fake OHLC. Body motion immutable drag-start snapshot. Magnet anchors only, never body/future. Selected unlocked handles use large invisible touch hitboxes. Locks rechecked at release/domain commit, locked drag native pan. Verify channel P3 and vertical-channel geometry, rectangle corner identity crossing, measurement timeframe remap vs elapsed wall time, clipping/HiDPI/styles/Fib hidden levels, loupe precision and restore paths. Renderer remains outside React; no pointermove setState or per-frame storage writes.

## Indicators/storage review
Files: src/indicators/*, src/storage/schema.ts/IndexedDBStore.ts, src/app/AppStore.ts, IndicatorPanel.tsx, DrawingSettings.tsx, WorkspaceSettings.tsx.

SMA, SMA-seeded EMA, actual Volume id-based and symbol-owned. Lock only visibility/unlock changes. Presets copy nested state and assign fresh IDs/symbols. Legacy Volume overlay persists for old symbols; explicit Volume takes control and removal must not resurrect the legacy overlay. DB2 upgrades old app/symbol records in a versionchange transaction; invalid migration aborts without erase. Export2 awaits queued writes and refuses storage failure; legacy1 import is validated then upgraded, unknown/invalid data rejected before one replacement transaction. Review orphan drawing alerts, duplicate IDs, symbol ownership, invalid levels/styles, and queued-save failure behavior.

## SEC/fundamentals review
Files: src/fundamentals/*; tests/fundamentals.test.ts, sec-client.test.ts, sec-provider.test.ts, sec-backend.test.ts, sec-expanded.test.ts; tests/fixtures/sec/*.json and both provenance files; docs/SEC_V1_VALIDATION.md.

Fixed official hosts, general ticker→CIK resolution/cache, truthful SEC User-Agent, throttle/dedup/cache/cooldown. Exact numeric/digit-string CIK validation normalized behind provider. Explicit US-GAAP concept priorities/unit/kind; null missing values. Conservative fiscal calendars and standalone-quarter extraction. Q2=H1−Q1, Q3=9M−H1, Q4=FY−9M only matching concept/unit/start/priorend and compatible filing vintage. Never subtract cumulative EPS. Audit raw inputs and derived formula/accessions/dates/units. CapEx positive outflow; FCF=OCF−CapEx only aligned sources. YoY/QoQ fiscal keys, zero denominator N/A. Scrutinize restatements/comparative facts, same-filing conflicts, stub years and unsupported custom/IFRS concepts.

JPM uses explicit RevenuesNetOfInterestExpense fallback, no overlapping interest aggregation. Seven issuer patterns: AAPL/MSFT/NVDA/TSLA/JPM/WMT/XOM. Official current XOM maps to CIK2115436 holding entity with limited actual facts; historical CIK34088 Exxon sample is clearly separate and never a resolver override. UI issuerName/CIK/audit identifies source. Filing markers use actual SEC filed dates and say SEC Filing, never assumed earnings timestamp.

## Backtest/signals correctness review
Files: src/strategy/*, tests/strategy.test.ts, BacktestPanel.tsx, ADR-006.

MA cross and price cross configured SMA/EMA. Caller explicitly supplies closed bars using MarketTiming scheduled session ends; current forming bars excluded. Signals compare warmed-up prior/current closed values; execution at NEXT available bar OPEN only. No next bar means no fill. Verify SMA-seeded EMA/prefix invariance, no-lookahead, gap fills, equality crossings, costs, short account cash/proceeds, marked open positions and metrics.

Single position sized100% current equity, entry reserves fees; no leverage. Zero default fees/slippage explicit, optional bps adverse fills and commissions on executed notional. Closed trades separate from still-open unrealized PnL; total return/drawdown marked equity, no fake forced liquidation. Short insolvency preserves actual nonpositive equity/open position, truncates subsequent simulation, states no broker margin model. PF uses net winning/loss trade PnL; null when no losses. Prototype data/corporate adjustment accuracy not certified; no real execution claim.

## Alerts/events/cache review
Files: src/alerts/AlertEngine.ts, AlertPanel.tsx, App.tsx, src/events/FilingEvents.ts, ChartEngine.ts, src/market-data/*, tests/alerts.test.ts/market-v1.test.ts/events.test.ts.

Local definitions enabled/disabled/deleted, symbol/timeframe scoped. Selected chart only, foreground app, one-minute normalized provider refresh. Initial history baseline suppresses retrospective spam, bar high-water mark dedupes, opposite condition re-arms. Re-enabling resets baseline. Drawing move resolves current price/reset; deletion invalidates/disables. Alert refresh never cancels chart gestures or resets backtest. No background/browser-closed/push claim. Review stale/cached closed bars and MA warmup semantics.

Provider capability controls timeframes; Demo7, Yahoo6 direct intervals (4H unavailable). Cache key provider/symbol/timeframe/range/kind, TTL, cloned results, dedup, bounded IndexedDB. Corruption/symbol mismatch/AbortError never falls back to stale; genuine unavailable network may return explicitly STALE CACHE. Offline missing data unavailable. Yahoo splits/dividends come from actual validated records; split numerator/denominator retained, unknown currency not guessed. No polling all watchlist quotes or paid feeds.

## Mobile/performance/security
Single chart stays primary. Phone sheets; iPad contextual side panel leaves chart interactive. Pointer capture/touch-action prevent page scroll. Controls44CSSpx with keyboard focus/form labels, no essential hover-only action. Escape/Delete/undo/redo skip input fields. Unified market/SEC/storage/import/strategy errors keep chart intact, opt-in Debug off by default. PWA production shell/offline local settings/Demo; no API caches pretending real-time. Native wrapper same Web assets/config/runbook, no signing claim.

Stress target2500bars,8mixedindicators,100MIXEDdrawings across tools, pan/zoom/crosshair/edit without runtime errors. Any RAF gap evidence is headless scheduling, not physical FPS, GPU/paint rate or finger latency. Physical-device drawing feel/loupe/touch/pinch/scroll/PWAinstall/safeareas/longsessions remain user UAT.

## Primary triage log (accepted fixes)
- 4H Demo history had insufficient bars: bounded generation now retains2500 real scheduled simulated slots without future OHLC; initial slow generation replaced with bounded weekday/session work.
- Vertical one-anchor renderer incorrectly required P2: exempted with renderer regression.
- Vertical body snapshot preserved time but drifted stored price: price delta forcedzero for vertical movement.
- Same-x channel baseline: horizontal width retained for a vertical channel; collapsed point finite.
- Fib custom hidden-level style defaults could reject new standard Fib: filtered defaults to standard levels, instance arrays copied.
- Backtest callback changed each parent render and reset output: stable callback.
- Arbitrary execution arithmetic could become non-finite: validation/overflow errors, commission bound, symbol normalization, deterministic tests.
- Numeric-string SEC CIK rejected matching identity: exact digit-string numeric validation/normalization, mismatch regression.
- JPM quarterly revenue missing despite actual bank-total concept: explicit mapped fallback after generic concepts.
- DB migration abort emitted unhandled tx.done rejection: completion promise observed while open/migration still fails safely; original database retained.
- Explicit Volume removal revived legacy overlay: symbol-owned compatibility flag prevents resurrection.
- Alert timer originally replaced chart dataset/backtest each minute: independent evaluation refresh, snapshot chart/manual refresh.
- Marker date alignment initially scanned all bars per event: bounded binary search on UTC calendar day.
- Drawing visibility was recoverable only via selection/settings: direct list Eye control added.

## Rejected / deliberately constrained
No current-XOM substitution to historical CIK, no guessed bank revenue sum, no cumulative EPS subtraction, no unsafe quarterly derivation, no fake future bars/events, no private chart API, no native Linux signing claim, no background local alerts, no physicalSafari equivalence from Playwright.

## Reviewer response format
Finding ID/severity/file/line/reproduction/evidence/proposed fix. Reviewer must NOT edit code. Primary will mark ACCEPT/REJECT with rationale; accepted changes get tests and rerun relevant/full gates. External reviewer sign-off must be recorded separately from this internal triage log.

## Browser integration triage (primary accepted, 2026-10-06)
- Mobile V1 selected-toolbar overlay blocked the existing locked Rectangle/Fib/Trend body-pan journeys after metadata/layout additions. Keep controls outside the plot in a stable-height status strip; preserve native pan and all old assertions. No pointer-system fork.
- WebKit datalist consumed ticker Enter without form submission, breaking symbol isolation journeys. Explicit input Enter normalizes/submits the current value while keeping form submission fallback. Original symbol tests rerun.
- Original baseline run intentionally stopped after diagnoses; failed gate is not claimed passing. Final four-project rerun will be recorded in V1_VERIFICATION.

- Primary accepted V1 mixed-object browser finding: DrawingPanel assumed every nonhorizontal object had P2 and crashed on one-anchor Vertical Line. List summary now checks actual anchor count and displays vertical date; mixed-tool list/regression rerun required.
- Cache browser test initially selected Demo rather than Yahoo metadata by omitting provider identity from its IndexedDB query. Accepted test-only identity filter; provider cache keys already distinguish both. Alert foreground/stale fallback browser tests passed before full rerun.
- Primary accepted finite strategy-ratio/aggregate overflow finding from internal execution-agent inspection: checked guards reject nonfinite ratios/sums, with executed extreme OHLC regressions. Marker persistence after closing research panel deliberately retained for chart inspection (not an orphan-symbol behavior).

- Primary accepted latent handler-restoration bug proved by stricter locked-channel pan regression: LWC 5.2.1 chart.options() returns a live options object, so disabling gestures mutated the saved restoration reference. Capture scroll/scale options by value before applying false. Release pointer capture with the actual up pointer ID after state-machine end. Existing lock-pan tests are strengthened to compare an already-defined range, preventing a first saved range from masquerading as movement. This is a Phase 1 hot-path fix required by V1, not a replacement pointer engine.
- V1 test helper incorrectly required an app record when only symbol indicators had been committed; default workspace legitimately has no app record yet. Read symbol records directly and await app record only when a workspace-setting commit is exercised. No product storage behavior change.

- Primary accepted a native Touch Event ownership bug after real CDP touch evidence: a drawing long press reached LWC's parallel Touch Event listener and entered crosshair tracking; the next locked-body drag then moved crosshair instead of panning, despite restored scroll options. The existing DrawingController now suppresses compatibility touch events only for owned drawing cycles through touchend; secondary touch cancels drawing and delegates native pinch. Geometry still uses Pointer Events and one RAF. The regression deliberately holds placement beyond the native long-press threshold and compares actual saved pan ranges. Temporary diagnostic reads of the installed open-source library were removed; production uses no private chart API.
- Primary accepted native CSS inset compatibility: use Capacitor's injected --safe-area-inset-* variables with standard env fallbacks. Core 8.5.2 injects zero when older Android WebView is already padded, so no duplicate inset is introduced; physical native edge-to-edge UAT remains pending.

## Browser evidence limits
Mobile Chromium drawing/pinch regressions dispatch real browser Touch Events through CDP. WebKit runs the iPhone/iPad device viewports and native mouse pointer capture with touch-modality overrides for drawing hitboxes/loupe; Playwright does not provide the equivalent CDP multi-touch path there. Those passing WebKit cases establish browser/layout/state behavior, not physical Safari finger latency, pinch feel or device FPS. Actual iPhone/iPad touch UAT remains mandatory before a device-quality sign-off.

## Final executed acceptance evidence
Clean root npmci PASS, unit135/135 across17files PASS, lint PASS, TypeScript/productionbuild PASS. Full88browsercases:78PASS,10expectedSKIP,0unexpectedFAIL,0flaky (desktop21/1, MobileChromium19/3, iPadWebKit19/3, iPhoneWebKit19/3). All core old/new journeys enabled. Skips only touch-only desktop and controlled desktop stress on otherprojects. Mixed2500/8/100all10tools plus101stcreate/edit/delete,pan/zoom/crosshair had zero runtime errors;89headlessRAF gapsmedian16.7p9516.8msare scheduling only. Nativeisolatedci/config/types/build-webPASS; finalnormalWebPWA buildrestored. ActualSEC7company/liveandlatestcompiledbackend4company audit smoke; YahooAAPL5m/1H/1D HTTP200. See docs/V1_VERIFICATION.md, v1-browser-summary.json, v1-performance-measurement.json, SEC_V1_VALIDATION.md and v1-live-*.json. No unfinished V1Cloud milestone or failing gate.

A source-only review archive is available at scratch/atlas-v1-review-source.zip. It includes code/tests/official test fixtures/ADRs/lockfiles/nativeconfig and this package, without node_modules, dist, credentials or local browser data. External reviewers should inspect the source as well as this narrative, return findings without editing, and avoid treating the passing suite as proof of universal SEC or real-device correctness.

## Authorized GitHub Pages hosting review — 2026-10-07

GPT-6.1 Sol High planned and independently read source without edits; GPT-6 Luna Max implemented frozen hosting-only changes. This is internal model engineering review, not external Gemini/Claude sign-off. Primary triage:

- ACCEPT HOST-01: remove unplanned Yahoo-backup rejection; preserve existing atomic import/provider preference, then explicit unavailable/manualDemo.
- REJECT HOST-02 recommendation to retain staticYahoo cachewrapper: backendabsence must remain explicit even iforiginhasoldcache; normalYahoo/Democache unchanged.
- ACCEPT HOST-03: exclude generateddist-pages fromESLint afterartifactbuild.

Reviewfiles: Vite/base/buildscript/workflow;HTMLlinks/mainregistration/nativeguard;manifest/scopedSWcacheandAPIfilters;staticprovider/UIguards;newPagesconfig/tests/ADR010/README. No chart/drawing/storage-model changes. Source snapshot comparison confirmshostingboundaries.

Historical intermediate gate evidence (superseded by the final results below): npmci;136unit/18files;lint;normal+Pagesbuild;8/8Pagesproductionbrowsercases coveringallfourprofiles, scopedcachecleanup/manifesticons, touchdrawing/locks/MAisolation/reload, portableYahoobackup/manualDemo, zeroAPIrequests, offlineDemo+SMA. Offlinecase specificallyrestoresSMA; onlinecase proveslocked-drawingreload. At that intermediate checkpoint the full normal regression and deployment were pending; final regression has since passed and publication is externally blocked as documented below. Do notclaim physicalSafari or externalreview.

PagescannotexecuteNodeYahoo/SECbackends; UIstatesunavailable, nofabricatedfinancials. No credentials inpublicationallowlist. GitHubPages administrationscopes mayrequireuseraccount action; sourcecommit successalone isnotlivewebsiteproof. IndexedDBoriginsharingbetweenprojectsdocumented; schema intentionallyunchanged.

Final hosting gate evidence: cleannpmci/136unit/lint/normal+Pagesbuild PASS; existingnormalV1browser78PASS/10expectedSKIP/0FAIL (8.1min); Pages8PASS/0FAIL (40.3sec), allfourprofiles. No Phase1/2/V1 regression. Publication remains a separate unverified step until actual HTTPS checks.

Historical authorization blocker (resolved by user authorization): publication attempts were BLOCKED: GitHubCLIcontentsPUT andconnectorcreate_file403; PagesPOST403; no commits/branches created; publicHTTPSsite404. Verified account/installation mismatch: authenticatedwudn9922, connectorinstallationonsaku0827/fast-launch-api only. Usermustauthorizetargetrepository. ThisisexternalGitHubauthorization, nottest/implementationfailure orauto-approvalreview rejection. No credentials inspected, exposed orbundled; no unauthorisedidentity/networkworkaround.

Primary ACCEPT HOST-04 documentation cleanup: mark intermediate pending-regression notes historical and put current external blocker/next action at top of PROJECT_STATE. Final Sol read-only review found no implementation blocker; actual owner authorization and HTTPS deployment remain external pending.

Source publication retry: userauthorizedwudn9922installation168757214;172files committedmain5f87026. HostedActionsci/unit136/lint/PagesbuildPASS; configurePagesonlyFAILbecause site notenabled. CurrentAppstillcannotcreatePages(403; missingadministration/pages). OwnerSourceGitHubActionssetting isnecessary externalsetup, supportedbyofficialconfigure-pagesactiondescription. No code/testfailure andnosecret/PATworkaround.

Primary ACCEPT HOST-05: update currentpublication summary withauthorizedinstallation/sourcecommit; markearlier no-commit/no-authorization paragraphs historical. Currentblocker isownerPagesenablement only; sourcecommit andhostedchecks succeeded.

## Live hosting completion

OwnerenabledPagesSourceworkflow; run37596025627 build+deploySUCCESS. HTML/allassets200 andremoteiPhoneWebKitDemo/SMAlockreload/PWAscope/staticSEC/noAPIs/noerrorsPASS withTLSverificationenabled. Evidence docs/pages-live-http.json/pages-live-browser-smoke.json. PhysicalSafariUATpending. RemoteCloudChromiumblockedbyproxyCAtrust; globalNSSCAmodificationwasrejectedbyautomaticreview duefuturetrustboundary andnotexecuted. No insecureTLSbypass. Alllocal136unit/86browserPASS+10expectedSKIP/lint/buildremainvalid; onlydocs/evidencechangeafterpublication. No V2featurework.

Primary ACCEPT HOST-06: GPT-6.1 Sol High finalread-onlyaudit confirmed liveHTTP/WebKitproof andaccurateCloudChromeBLOCKEDstatus; GPT-6 Luna Max relabeledthe earlierunqualifiedPublicationheading ashistorical. No applicationcodechange. Latestdocs/evidence deploymentrun37596875057 build/deploySUCCESS beforethis finalheading-onlycleanup.


## SEC material restated comparatives — PR #153 share-basis/Q4 revision (2026-10-22)

Merged latest main 71bcd1cacd0169286d8353e7e2dfa610c6eff57e into the original codex/sec-restated-comparatives-20261022 branch using merge commit d12e510b6fe82e39ae0b7daa444f0bb2bd25ac1b. The sole conflict was this review package; both sides were retained. No rebase, force-push, PR merge or data apply.

Only a later annual filing with a strictly greater than 2% difference against a current non-per-share income field may switch a period. EPS and dilutedShares cannot trigger switching. Per-share-only switches drop from 379 to zero. Existing per-row split/ADR conversion remains, followed by a conservative integer-factor retention guard (1% relative tolerance; also checks income-adjusted implied share ratios). Suspected share-basis candidates retain all existing per-share fields, recorded as basis-unrestated and mixed-basis, without claiming a ratio alone proves a corporate action. The reported CAPR, GGAL, EJH and HAO examples all remain unchanged.

Missing filing fields retain their current value with existing flags preserved. When Q1–Q3 are not available on the switched annual filing's basis, the entire stored quarterly series, including Q4, remains unchanged; annual and quarterly period issues identify quarterly-as-reported and retain the reason preventing cross-basis derivation. Unapplied quarter candidates do not acquire restatement revisions. Complete coherent restated quarters still permit the unchanged numeric/share/sign/error acceptance gates. No existing Q4 EPS is set to null.

Final dry run: 4,967 US files, 4,960 reviewed and seven explicitly unsupported; 1,172 switched annual periods in 580 symbols (788 revenue/netIncome-triggered periods in 425 symbols); 2,151 changed values. All nulls, including Q4: zero. All 1,599 formerly nulled Q4 EPS cells are retained. Two Q4 cells change: KVHI FY2013 basic/diluted -0.02 to 0.36, directly reported by the same restating filing. There are 1,152 quarterly-as-reported fiscal years. AD FY2023/FY2024 revenue becomes 100,469,000 / 102,933,000, while both quarterly series and Q4 EPS stay unchanged.

The feedback's 33-of-381 count cannot be reproduced literally from the pinned 8f0a8b8e report: it contains 382 annual diluted-EPS changes (376 have two non-null endpoints), and the named factors with relative error <=1% yield 25 cases. All 25 retain existing EPS. JSON shareBasisAudit includes every one of the 382 before/candidate/after outcomes and the exact formula; Markdown also lists a wider 58-case screen for rounding-sensitive review. This wider set is explicitly not proof of a split. No arbitrary list was relabelled as the requested 33 cases.

The full US snapshot was regenerated offline from the existing cache; after the final unapplied-quarter audit guard, all 18 files containing quarterly retention revisions were replayed, with unchanged numeric results. All 4,967 input hashes match the previous report and current files. All null/drift invariants pass. This revision sends zero SEC requests; original identified-UA, <=4 requests/second, first-403 stop and 12,000-request budget remain unchanged.

Reproduce the comparison audit by exporting the prior report first:

```bash
git show 8f0a8b8e:docs/report/sec-restated-comparatives.json > /tmp/sec-restatements-before-share-fix.json
python atlas/scripts/report_sec_restatements.py --prefer-latest-filing --offline --workers 4 --cache-dir /workspace/.cache/sec-restatement-cache --comparison-report /tmp/sec-restatements-before-share-fix.json
```

Final verification: python -m unittest discover -s tests — Ran 1493 tests in 186.294s, OK. All existing cases remain, with superseded null expectations updated to the new preservation policy; 23 focused restatement tests cover the AD example, coherent restated quarters, per-share-only rejection, share-factor retention/audits and switched-year quarterly-as-reported preservation. Main's new test dependencies (xlrd/PyYAML) were installed before the successful full rerun. Atlas: clean npm ci; 964 tests passed; lint and build passed. An initial frontend run during the report hit a 5-second fixture timeout; the unchanged full suite passed after the scan.

No financials data file, frozen Q4 answer set, missing-classification file, acceptance ledger or numeric acceptance gate was changed. Default-off selection remains unchanged; no CLI apply path exists. Independent review is unavailable; no independent sign-off is claimed. New-head CI is tracked in the PR description.

## 2026-10-22 public research extension — same-model self-review

User requested one-model continuous implementation and explicitly prohibited merging. No independent AI review was requested or claimed. See [the acceptance matrix](../docs/RESEARCH_ACCEPTANCE.md) and [source/method contract](../docs/RESEARCH_DATA.md). Primary self-review verified official category totals and CSV report dates, excluded today's volume from RVOL denominators, retained directional numerators/denominators and missing-state distinctions, rejected pre-separation institution categories instead of synthesizing zero, retained 649 newer report snapshots over older cache output, deduplicated SEC accessions across share-class aliases, persisted cooldown, and kept monthly/weekly data out of daily returns. The new chart dock state is transient and does not enter user settings/export schema; chart/indicator/drawing/LINE/authentication implementations were preserved. Local stock/drawing/timeframe regressions passed; final CI is reported by PR #152. Production has not been changed by this PR.

Official daily turnover follow-up: verified dated TWSE MI_INDEX and TPEx dailyQuotes responses; preserved raw market inputs with a 120-session cap. Institutional estimates use amount/shares only from the same official source, exclude incompatible closing-price bases, and still disclose market proxies rather than actual institutional cost. Cash-only enrichment preserves canonical OHLCV signals after cache eviction; tests compare it against full calculation. The full Python CI now checks out repository history required by legacy regression tests.

## PR #152 operations follow-up — same-model self-review

Accepted: recursive clock-insensitive writes; seven market archives with stable shared shape tables; range/200 transport and identity validation; preserve all 10,384 original records including US ticker TW. Run clocks remain in module indexes, SEC event receipt times rehydrate from its index. No chart/storage/auth boundaries changed.

Accepted: two source-timed schedules, market-selective volume, fresh Taiwan turnover artifact dependency, one non-force publish commit per run, collectors read-only without persisted credentials, checksum/path validation before overlay, main-only publication and research-code race guard. Missing SEC secret skips with no request and no snapshot change; fallback identity removed from the radar.

Measured payload 158,722,480 → 103,409,630 bytes, 10,389 → 12 files. Full real-data clock-only replay made no byte/mtime changes. Growth replay measured 1,909,836 bytes delta-packed; budget 2–4 MB/day packed, or roughly 23 MB/day before server packing, with documented source/backfill uncertainty. Existing PR history retained, no force push. No production run, merge, external AI or independent-review claim. Evidence and reproducible commands: `docs/RESEARCH_OPERATIONS.md`.

## 可畫線世界／題材圖表自我審查

以 main adfb6c7da 為基準另開 PR。重用 5.2.1 ChartEngine 與 Drawing Primitive；AppStore 初始化抽成共用 singleton，沒有新增資料庫或改登入，研究 namespace 與週期隔離。原物料／FX 真實日 OHLC；期貨明示 source-stitched front contract，FX 保持方向並排除無法通過 OHLC 邊界的來源資料。題材選參考 ETF／成分股，沒有假造等權組合高低價；其他題材統計／清單保留。

只新增 K 線永久 CSV，日期合併拒絕刪歷史；current metadata 與 JSON 視圖覆寫。白名單／checksum artifact handoff 延伸至 CSV；generated publisher records 將其保留 main。沒有操作正式開關、force main、合併或刪除既有資料。覆蓋、實際樣本、供應商限制、重現指令：docs/DRAWABLE_RESEARCH_CHARTS.md。同模型自我審查，沒有外部 AI。
# Task A data integrity review (2026-10-22)

Primary-engineer self-review; no independent sign-off. User scope: one PR, no merge.
See [data, source receipts and validation](../docs/DATA_INTEGRITY_TASK_A.md).
Reviewed acceptance/refetch/official fallback, permanent-row preservation, quarantine before
indicators and calendar aggregation (including offline cache), legitimate negative financial
net revenues, explicit-null merge precedence, same-date RS ranking and screener date scopes.
The known 7825 row remains stored and excluded; no unverified price was invented.


## Task B — reviewed SEC comparative apply (2026-10-23)

Primary implementer self-review; no independent human sign-off. Separate PR, do not merge.

- Apply pinned #153 report only: 580 symbols, 1,172 annual periods, 2,151 cells; zero nulls, zero lost flags. Every changed original retained in quality.revisions(kind=restated).
- Independent Git-tree verification compares all 6,947 financial files, retains alignment and EPS grades, proves exact old/new cell equality and all 9 Q4 answer/classification/ledger files byte-identical.
- 219 growth rows independently rebuilt; unrelated rows retained. NVDA metadata-only secEnd exposes FY2026 2026-01-25 beside unchanged 2026-01-31.
- Offline preflight rejects drift before any write; batch rolls back on write failure. SEC fetching is separate, identified and bounded; apply makes zero requests.
- SEC-focused: 39 passed, full financial-tree / growth / protected-file proof passed. Initial full Python at the Task A data baseline: 1,582 passed; after main research record updates: 1,584 run, one existing world snapshot/CSV consistency failure. Atlas clean npm ci, 985 tests / 84 files, typecheck, lint, production and Pages builds passed. Four new local desktop/mobile Chromium checks passed; first remote browser run: 230 passed / one desktop-inapplicable skip. Overall CI is not green because of the world input mismatch; no test was relaxed. Independent sec-write-proof CI runs regardless of that integration result.
- Evidence: docs/SEC_RESTATED_COMPARATIVES_APPLY.md and docs/report/sec-restated-comparatives-{apply,verification}.json.

Task B refresh regression: ordinary updates retain restatement flags, original revision history and secEnd; explicit reviewed SEC basis protects the five legacy-Yahoo-labelled annual rows from being reverted by Yahoo. No additional statement values were written.

## FY2021–FY2025 conservative Yahoo backfill (2026-10-23)

Primary implementer self-review; no independent human sign-off. Replacement for closed, unmerged #151;
new branch from main containing #164; do not merge. See [policy and replay](../docs/YAHOO_ANNUAL_BACKFILL.md).
Reviewed whole-period 2% veto across every overlapping field, zero/negative/unit handling, no EPS/shares,
null-only existing annual rows, preserved unknown flags/revisions, field provenance in notes/table/chart detail,
Yahoo/SEC/FinMind refresh retention, atomic batch rollback, and independent Git-tree + protected Q4 proof.

Shared provenance notes distinguish annual and quarterly field fills. The existing WebKit MA layout
regression assumed both desktop valuation and mobile tab elements existed while accepting either as
the shared row; it now waits for the toggle and measures each available sibling. The same-row and
zero-overflow assertions remain mandatory; no MA product behavior changed.

## Monthly MA, LINE trending stocks and single-symbol refresh (2026-10-27)

Primary-engineer self-review; no independent sign-off. User requested a PR without merging;
the follow-up changes MA cadence from the original two-month request to monthly.

- MA job runs on the first of each month at 02:17 UTC (10:17 Taipei), with manual dispatch and existing partial-failure retention intact. UI cadence text matches.
- The LINE report saves its ordered, at-most-five US trending symbols atomically after a successful push. Empty fetches preserve the last good report and its timestamps. The daily generated owner, ignored-output inventory, Pages assembly and legacy publication include this file. No recipient identifiers or message text are published.
- Screener and US markets read the same schema-validated JSON; loading/error/retry and aged-data states are explicit. Symbol buttons open the existing daily chart.
- Manual K-line refresh bypasses the selected symbol's normalized/raw/HTTP caches, validates results, keeps other symbols' caches, and protects refreshed entries against earlier in-flight writes. Weekly/monthly normalization still uses that symbol's daily rows. Failed refresh keeps the current chart; navigation cancels old refreshes. Existing drawing/indicator/view state is reused. The source remains Yahoo delayed or official static data.
- Verified clean `npm ci`, 1,315 Atlas tests (113 files, final full run with `--maxWorkers=1`), lint/typecheck, normal and screener Pages builds. Python: 2 trending-report tests, 24 LINE/screenshot regressions and 28 generated-storage tests. Generated-storage coverage proves the new report survives publication and Pages assembly. Initial concurrent full testing hit a five-second SEC fixture timeout; the isolated complete rerun passed without changing the test.
- Browser regression: 29 passed / 1 existing desktop-inapplicable skip across the new feature, MA recommendations, market-chart controls and workspace layout/symbol preferences on desktop Chromium, mobile Chromium and iPhone WebKit. The compact refresh button fixes the discovered phone-header height regression. WebKit's missing libraries were extracted into `/tmp`, checked with `ldd`/`ctypes`, then exercised with host validation disabled because `ldconfig` cannot discover the local libraries. This is headless evidence, not physical-device UAT.
- Read-only live smoke: the existing Supabase `market-chart` endpoint returned AAPL and 21 daily bars for `range=1mo`; no production function deployment, real LINE push or MA/data job was triggered. First production trending report appears after the next successful daily LINE report following merge.

## PR #186 requirement revision — US screener LINE group

Merged main/PR #185 with merge commit `dfab5672c`; both parents and both browser test lists are retained.
`index-volume.spec.ts` is byte-identical to main: the first three market cards remain index-volume,
breakout and breadth. Both standalone trending cards and their stylesheet are removed.

The US-only `LINE 熱門` group immediately follows the full-market chip. Its default order is the LINE
symbol order; each existing universe row retains its metrics, dates and profile, while absent symbols
remain explicit unmatched rows. The same screener rows, pills, sorting, filters and chart actions apply.
Saving uses the existing manual-watchlist format, retaining this set of symbols and the applied filters;
it does not accidentally save the whole US universe. File errors leave the chip visible with the required
missing-list message and retry. Fetch/send timestamps are shown, and stale status compares the fetched
Taipei day to the newest report date rather than a 36-hour wall-clock threshold.

Python changes only add the frontend symbol pattern as a skip-invalid validation before deduplication
and the five-symbol cap. Last-good retention, post-send saving, generated ownership and Pages publishing
remain unchanged. Local verification: clean npm ci; 1,322 Atlas unit tests; three trending Python tests,
24 existing LINE/screenshot tests and 28 generated-storage tests; the eight targeted desktop browser cases
passed. Phone/WebKit and the complete Atlas PR workflow are rerun for this revision; final CI results are
reported with the PR, without claiming independent review, deploying or merging.

Phone visual follow-up: prevent the fixed-group status tabs from shrinking under the LINE timestamp
and stale notices, and tighten the timestamp paragraph spacing. The group browser test checks that the
status-control container cannot clip its buttons. All 24 targeted browser cases passed on the preceding
revision; the same matrix and the full PR workflow are rerun on this layout follow-up.

## 導覽改版第一階段（main d59b07be4）

主要工程師自我審查，沒有宣稱獨立審核。使用者範圍為導覽與內容搬移，所有功能／控制／網址／資料都必須保留；沒有無法保留而被刪除的項目。

功能與舊網址對照、修改過的每個既有 spec、文件標題差異、390px／桌面截圖及 trace 說明見 docs/NAVIGATION_PHASE_ONE.md。MarketsPage、WorldPage、ThemesPage、ChartEngine、StorageNotice、ParameterConsole、main.py、SQL、Supabase、workflow 與市場資料沒有修改。清單與原有雲端／歷史邏輯共用原 ConsolePage，只調整 section 與導航目的地。

原 DEFAULT_PAGE、品牌連結、日期 chip、DataHealthPanel、RouteBoundary、lazy loading、chartVisited 及捲動邏輯保留。入口測試證明圖表／財務往返仍是同一 chart DOM；帳號／舊資料／LINE／PWA／清單整合斷言完整保留。先前桌面新測試使用 radio 的隱藏 input 點擊，已改用原可點擊 label，保留 checked 與重載後持久化斷言。

已執行 clean npm ci、npm test（1,505 通過）、lint、一般與 Pages build；完整 npm run test:site 的三瀏覽器結果、其失敗與修正記錄以 PR body 為準。不合併、不部署。

全量回歸發現 main 的 screener-universe-full.spec.ts 在原本收合的名單範圍說明中直接檢查內文。測試補上點擊既有按鈕的操作，原有內文、收合／展開、數量、篩選與虛擬列表斷言全部保留；產品的說明按鈕和展開行為未改。

## 後續授權：本益比篩選

本益比獨立 PR 以 main `087f4f113` 為基準；既有導覽改版及後續報價板、個股分頁、市場分類已在主線，不包含在本次差異中。

完整回歸發現 world-btc.spec.ts 在 BTC 保存來源切換時捲動卡片，會遇到 DOM 替換而偶發 element detached。原文字、OHLC、日曆、畫布、週K與零錯誤斷言全部保留；先等待原有來源日期／保存標示，再於 10 秒內重試對同一 locator 的捲動，進入畫面後才等待延遲載入圖表的 OHLC。WorldPage 與行情程式未改。修正後三平台 BTC 回歸另行重跑，精確結果記在 PR。

使用者要求繼續增加本益比選項。本次沿用原估值與篩選架構，新增可留白的上下限及快捷倍數；舊條件、導覽與網址全部保留。具體規則、資料缺漏處理及測試見 docs/SCREENER_PE_FILTER.md。市場檔只載入一次且涵蓋畫面外股票，來源失敗不會回傳未篩選結果；自選策略保留失敗前的比較基準。離線收集器只適配既有估值 JSON，未變更任何後端 API、SQL、權限、workflow 或市場資料檔。採主要工程師自我審查，沒有獨立簽核；實際測試數字記錄在 PR body。
