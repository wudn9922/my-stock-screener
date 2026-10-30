/*!
 * yield_band.js — 公債殖利率「合理區間」(回歸法),無外部相依,瀏覽器 / Node 都能用
 *
 * 公式:  殖利率 ≈ a + b × 政策利率(目標區間上限)
 *        中心   = a + b × 目前政策利率
 *        下緣/上緣 = 中心 ∓ z × sd        (z = 1.2816 → 80% 區間;sd = 殘差標準差)
 *
 * 參數 a、b、sd 由 yield_band.py 每月更新到 band_params.json;
 * 網頁只需讀 JSON + 政策利率就能算,不必在瀏覽器裡跑回歸。
 * (若想在瀏覽器直接用歷史資料擬合,用 fitBand(y10Array, policyArray)。)
 *
 * ── 網頁用法 ─────────────────────────────────────────────
 *   <script src="yield_band.js"></script>
 *   <script>
 *     fetch('band_params.json').then(r => r.json()).then(P => {
 *       const jp = P.JP;                                   // 或 P.US
 *       const b = YieldBand.bandAt(jp, jp.policy_now);     // {lo, center, hi}
 *       const z = YieldBand.zScore(jp, jp.policy_now, 2.83);
 *       console.log(b, z, YieldBand.status(z));
 *     });
 *   </script>
 *
 * ── Node ────────────────────────────────────────────────
 *   const YB = require('./yield_band.js');
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.YieldBand = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var Z80 = 1.2816;     // 80% 雙尾;90% 用 1.645、95% 用 1.96

  function mean(a) { var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return s / a.length; }
  function sampleSd(a) {
    var m = mean(a), s = 0;
    for (var i = 0; i < a.length; i++) s += (a[i] - m) * (a[i] - m);
    return Math.sqrt(s / (a.length - 1));
  }

  /** 全樣本 OLS:y10 = a + b*policy + e。回傳 {a,b,sd,n,spread_mean,spread_sd}(欄位名與 band_params.json 相同) */
  function fitBand(y10, policy) {
    var n = y10.length, mx = mean(policy), my = mean(y10), sxy = 0, sxx = 0, i;
    for (i = 0; i < n; i++) { sxy += (policy[i] - mx) * (y10[i] - my); sxx += (policy[i] - mx) * (policy[i] - mx); }
    var b = sxy / sxx, a = my - b * mx, sse = 0, spr = [];
    for (i = 0; i < n; i++) { var e = y10[i] - (a + b * policy[i]); sse += e * e; spr.push(y10[i] - policy[i]); }
    return { a: a, b: b, sd: Math.sqrt(sse / (n - 2)), n: n, spread_mean: mean(spr), spread_sd: sampleSd(spr) };
  }

  /** 區間。p = {a,b,sd,...}。method: 'regression'(預設,圖上用的)或 'spread'(利差法:政策利率 + 平均利差) */
  function bandAt(p, policy, z, method) {
    z = z == null ? Z80 : z;
    var c = method === 'spread' ? policy + p.spread_mean : p.a + p.b * policy;
    var s = method === 'spread' ? p.spread_sd : p.sd;
    return { lo: c - z * s, center: c, hi: c + z * s };
  }

  /** 殖利率偏離中心幾個標準差。>0 偏高(債價偏低)、<0 偏低;±1.2816 = 區間上下緣 */
  function zScore(p, policy, y10) { return (y10 - (p.a + p.b * policy)) / p.sd; }

  /** 文字狀態(門檻 1.2816 = 80% 區間邊界) */
  function status(z) {
    if (z >= Z80) return '高於區間上緣';
    if (z >= 0.5) return '區間內偏上';
    if (z > -0.5) return '區間中段';
    if (z > -Z80) return '區間內偏下';
    return '低於區間下緣';
  }

  /** 樣本外區間序列(第 t 筆只用 t 筆以前的資料估計)。回傳 [{i, lo, center, hi, z}],minN 預設 120(個月) */
  function oosBands(y10, policy, minN, z) {
    minN = minN || 120; z = z == null ? Z80 : z;
    var out = [];
    for (var t = minN; t < y10.length; t++) {
      var p = fitBand(y10.slice(0, t), policy.slice(0, t)), bd = bandAt(p, policy[t], z);
      out.push({ i: t, lo: bd.lo, center: bd.center, hi: bd.hi, z: (y10[t] - bd.center) / p.sd });
    }
    return out;
  }

  return { Z80: Z80, fitBand: fitBand, bandAt: bandAt, zScore: zScore, status: status, oosBands: oosBands };
});
