# MEXC Spot V3 文件形狀

2026-11-06 核對官方文件：

- `api-key-info.official.json`：[Query APIKey Info](https://www.mexc.com/api-docs/spot-v3/spot-account-trade/query-api-key-info) 的公開假Key範例；它包含WRITE權限，必須拒絕。成功唯讀測試只把權限改成READ，形狀與別名不變。
- `account.official.json`：[Account Information](https://www.mexc.com/api-docs/spot-v3/spot-account-trade/account-information) 的公開回應範例；available不是額外餘額。
- `ticker.official.json`：[Symbol Price Ticker](https://www.mexc.com/api-docs/spot-v3/market-data-endpoints/symbol-price-ticker) 的陣列範例。

全部是假資料或官方公開範例，不是使用者持股或真實金鑰。code:0包裝案例為兼容性測試，不冒稱這三個端點的文件範例或已觀察到的正式回應。
