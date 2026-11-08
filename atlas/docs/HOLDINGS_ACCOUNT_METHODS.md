# 新增持股帳戶

新增方式只有「API 自動同步」與「匯入 CSV」。

- API：MEXC（加密貨幣）沿用原唯讀連線、餘額與成本確認流程；永豐證券（台股）顯示即將推出，提交按鈕停用。
- CSV：所有券商共用，使用者填寫帳戶名稱，再選台股、美股或加密貨幣。永豐美股請用匯入 CSV。
- 新 CSV 帳戶一律 broker=other，市場分別對應 tw-broker/TWD、us-broker/USD、crypto-exchange/USDT；MEXC API 保持 broker=mexc。永豐尚未開放建立 API 帳戶。

沒有更改資料 schema、broker enum 或後端驗證；既有國泰、永豐、凱基、口袋、Firstrade、MEXC 帳戶持續照原資料使用。CSV/Excel/貼上、欄位對應、預覽修正、略過列、成本確認與既有帳戶重新匯入均保留。

瀏覽器測試更新新增表單的入口，保留原有持股與 MEXC 斷言；另驗證永豐停用、三個 CSV 市場、自填名稱，以及實際美股匯入後保存 broker=other。

390px 截圖見 [新增方式](screenshots/holdings-account-methods-390.png)。Playwright 模擬瀏覽器，並非實機驗證。實際驗證結果列於 PR；僅 primary self-review，沒有獨立簽核。

驗證：相關單元12檔／290項、完整單元165檔／2,172項通過；lint與build通過；holdings、holdings-hide、mexc三份Playwright spec在桌機Chromium、390px Chromium與iPhone WebKit共99項全部通過（7.2分鐘）。
