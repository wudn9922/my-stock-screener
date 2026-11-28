/**
 * Atlas 對帳單同步（貼到 https://script.google.com 的新專案）。
 *
 * 每天把券商寄來的 PDF 對帳單送到 statement-sync 服務解析；處理完成的信會加上
 * 「Atlas/已同步」標籤，之後不再重送。密碼錯誤或格式尚未支援的信不加標籤，隔天會再試。
 *
 * 指令碼屬性（專案設定 → 指令碼屬性）：
 *   STATEMENT_URL     服務網址，例如 https://statement-sync-xxxx-uc.a.run.app
 *   STATEMENT_SECRET  與 Secret Manager 的 statement-shared-secret 相同
 *   STATEMENT_SENDERS 選填，額外的寄件網域，逗號分隔。券商網域由服務提供，新增券商不必改這份程式。
 */
const LABEL = 'Atlas/已同步';

function syncStatements() {
  const props = PropertiesService.getScriptProperties();
  const base = (props.getProperty('STATEMENT_URL') || '').replace(/\/+$/, '');
  const secret = props.getProperty('STATEMENT_SECRET') || '';
  if (!/^https:\/\/[^/]+\.run\.app$/.test(base) || secret.length < 32) throw new Error('請先設定 STATEMENT_URL 與 STATEMENT_SECRET');
  const senders = brokerSenders(base, secret)
    .concat((props.getProperty('STATEMENT_SENDERS') || '').split(','))
    .map((s) => s.trim().toLowerCase()).filter((s, i, all) => /^[a-z0-9.-]+$/.test(s) && all.indexOf(s) === i);
  const label = GmailApp.getUserLabelByName(LABEL) || GmailApp.createLabel(LABEL);
  const query = '(' + senders.map((s) => 'from:' + s).join(' OR ') + ') has:attachment filename:pdf newer_than:60d -label:"' + LABEL + '"';
  const results = [];
  for (const thread of GmailApp.search(query, 0, 30)) {
    let done = true;
    for (const message of thread.getMessages()) {
      message.getAttachments({ includeInlineImages: false }).forEach((file, index) => {
        if (!/\.pdf$/i.test(file.getName()) && file.getContentType() !== 'application/pdf') return;
        const response = UrlFetchApp.fetch(base + '/ingest', {
          method: 'post',
          contentType: 'application/json',
          headers: { 'X-Statement-Secret': secret },
          muteHttpExceptions: true,
          payload: JSON.stringify({
            messageId: message.getId() + ':' + index,
            from: message.getFrom(),
            receivedAt: message.getDate().toISOString(),
            pdf: Utilities.base64Encode(file.getBytes()),
          }),
        });
        let result = {};
        try { result = JSON.parse(response.getContentText()); } catch (e) { result = {}; }
        results.push(message.getDate().toISOString().slice(0, 10) + ' ' + (result.code || response.getResponseCode()));
        if (!result.done) done = false;
      });
    }
    if (done) thread.addLabel(label);
  }
  console.log(results.length ? results.join('\n') : '沒有新的對帳單');
}

/** 服務支援的券商寄件網域；服務暫時連不上時用內建清單。 */
function brokerSenders(base, secret) {
  const fallback = ['cathaysec.com.tw', 'sinopac.com', 'sinotrade.com.tw', 'kgi.com', 'kgi.com.tw'];
  try {
    const response = UrlFetchApp.fetch(base + '/senders', { headers: { 'X-Statement-Secret': secret }, muteHttpExceptions: true });
    const list = JSON.parse(response.getContentText()).senders;
    return Array.isArray(list) && list.length ? list.map(String) : fallback;
  } catch (e) {
    return fallback;
  }
}

/** 執行一次即可：建立每天下午 2 點的自動觸發。 */
function installDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'syncStatements')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncStatements').timeBased().everyDays(1).atHour(14).create();
}
