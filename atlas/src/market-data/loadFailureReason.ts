/** Public market-data failures only. Never echo arbitrary response bodies, URLs or stacks. */
export function loadFailureReason(error: unknown): string {
  const text = error instanceof Error ? error.message : "";
  if (/timeout|timed out|逾時/i.test(text))
    return "行情來源回應逾時，請稍後重試";
  if (/429|頻率/.test(text)) return "行情來源請求過多（HTTP 429），請稍後重試";
  if (/404|找不到|下市/.test(text))
    return "來源找不到此代號，可能尚未上市或已下市";
  if (/403|拒絕/.test(text)) return "行情來源拒絕連線（HTTP 403）";
  if (/502|503|504|服務暫/.test(text))
    return "行情服務暫時無法使用，請稍後重試";
  if (/JSON|驗證|valid|schema|格式/i.test(text))
    return "來源回傳的資料格式無效或缺少必要欄位";
  if (/未設定|not configured/i.test(text)) return "此網站尚未設定行情來源";
  if (typeof navigator !== "undefined" && navigator.onLine === false)
    return "裝置目前沒有網路連線";
  if (/fetch|network|連線|無法取得/i.test(text))
    return "無法連線至行情來源，請檢查網路後重試";
  return "行情來源未提供可用資料，請稍後重試";
}
