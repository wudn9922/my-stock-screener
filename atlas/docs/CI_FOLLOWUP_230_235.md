# PR #230／#235 的瀏覽器驗證修正

只修正測試的觀測／頁面就緒條件，沒有修改產品、登入、帳號或儲存程式，也沒有新增 skip、增加 timeout 或放寬原斷言。新增的新版頁面請求斷言讓部署切換驗證更完整。

## 導頁復原的成功旗標被相依檔覆寫

失敗執行：[PR #230 run 42218359110](https://github.com/wudn9922/my-stock-screener/actions/runs/42218359110)、[PR #235 run 42217958233](https://github.com/wudn9922/my-stock-screener/actions/runs/42217958233)。兩者都在 `navigation-recovery.spec.ts:70` 等待 `newDeployment`，其餘畫面已載入。只有本機重跑通過不足以說明原因，因此實際下載 #230 的 browser evidence artifact **13503448563** 檢查 trace 和截圖。

Vite 在同一個部署會產生多個 `index-*.js`。舊測試對每個符合這個 glob 的回應，都執行 `newDeployment = body.includes('?deployment=new')`。若不含 WorldPage 的相依模組較晚回應，就把已成功的 true 改回 false。

失敗 trace 中 reload 後的實際請求順序：

| 請求 | HTTP | 證據 |
| --- | --- | --- |
| `index-DrDaN5sq.js` | 200 | entry 的 WorldPage URL 改成新版 query |
| `WorldPage-kgQ_mfxd.js?deployment=new` | 200 | 真正的新版懶載入檔已請求並載入 |
| `index-CH87S-1G.js` | 200 | 不含 WorldPage 的相依模組較晚完成，覆寫旗標 |

最後失敗截圖已顯示全球頁的美債／指數／日債等頁籤，沒有 RouteBoundary 錯誤框；失敗的是測試旗標。修正為「觀察到新版 entry 就保留 true」，同時新增實際 `WorldPage-*.js?deployment=new` 請求的斷言。原本的重新載入、全球頁可見、錯誤框消失及先切換選股再回來的驗證全部保留。

## 等待「我的」懶載入頁面後再操作頁尾

#230 的同一份 CI evidence 另有 iPhone WebKit cloud legacy-import 失敗。頁尾 LegacyImportCard 在 Suspense 外，會先於 ConsolePage 出現；只等卡片 visible 不代表卡片上方的懶載入頁面已完成排版。

該 trace 的點擊時間為 1043659.812–1043788.538 ms，而 ConsolePage 的 groups.list、stocks.list 等初始請求在 1043788 ms 才開始；沒有執行匯入流程應有的 access.get。最後截圖回到控制台頂端，卡片仍是 available，不能把這個結果當成伺服器拒絕匯入。

測試現在在第一次與重開匯入提示前，先驗證「均線參數與個人分組」標題可見、初始讀取完成後「重新載入參數」按鈕啟用，再捲動／點擊頁尾。使用原本的可操作性與匯入結果斷言，不使用 force click、固定等待或重試點擊；衝突保留、舊資料 bytes 不變、雲端只存自己的資料、重跑無重複等檢查全部保留。

## 驗證範圍

兩個既有檔案以原設定在桌機、390px Chromium／iPhone WebKit 執行；legacy-import 另跑375px Chromium／WebKit。各分支的實際結果列於 PR 與 REVIEW_PACKAGE，完整 CI 對最後 head 重跑。原失敗後被擋住的離線 PostgreSQL 與 breakout integration 也必須完成，不能略過。此文件不把尚未完成的 CI 預記為通過。

只有 primary self-review；trace 為合成 fixture 的自動化瀏覽器，不是實體 iPhone 或正式帳號驗收。沒有連線真實 MEXC／Supabase 個資，沒有部署、資料修復或通知。
