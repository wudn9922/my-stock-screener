/* Runs before the module bundle: never reads login URLs, secrets or storage. */
(function () {
  function message(text) {
    var welcome = document.getElementById('atlas-welcome');
    if (!welcome) return;
    document.getElementById('atlas-boot-status').textContent = text;
    document.getElementById('atlas-boot-retry').hidden = false;
    var progress = welcome.querySelector('progress');
    if (progress) progress.hidden = true;
  }
  document.getElementById('atlas-boot-retry').onclick = function () { location.reload(); };
  if (navigator.onLine === false) message('目前沒有網路連線。連線恢復後請重新載入。');
  window.addEventListener('offline', function () { message('目前沒有網路連線。連線恢復後請重新載入。'); });
  window.addEventListener('error', function (event) {
    if (event.target instanceof HTMLScriptElement || /import|module|fetch/i.test(event.message || ''))
      message('應用程式檔案載入失敗，可能是連線中斷或網站剛更新。請重新載入。');
  }, true);
  document.addEventListener('atlas-startup-error', function () { message('Atlas 初始化失敗，請重新載入。若仍失敗，請確認瀏覽器允許此網站執行。'); });
  setTimeout(function () { message(navigator.onLine ? '載入時間較長，可能仍在等待網路或瀏覽器初始化。可以繼續等待或重新載入。' : '目前沒有網路連線。連線恢復後請重新載入。'); }, 15000);
}());
