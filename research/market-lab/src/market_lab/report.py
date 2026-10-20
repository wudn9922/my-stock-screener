from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _fmt(value: Any, percent: bool = False, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    return f"{number * 100:.{digits}f}%" if percent else f"{number:.{digits}f}"


def write_data_coverage(path: Path, coverage: pd.DataFrame, issues: pd.DataFrame, repo_sha: str, run_date: str) -> None:
    equities = coverage.loc[coverage["instrument_type"] == "equity"].copy() if not coverage.empty else coverage
    rows = [
        "# 資料覆蓋率",
        "",
        f"- 來源 repo main SHA：`{repo_sha}`；執行日期：{run_date}。",
        "- 原始日 K 欄位：Date、Open、High、Low、Close、Volume。來源程式對 Yahoo 設 `period=1y`、`auto_adjust=False`、`actions=False`。",
        "- 覆蓋率逐檔資料見 [per-symbol-coverage.csv](per-symbol-coverage.csv)，品質旗標見 [quality-issues.csv](quality-issues.csv)。",
        "",
        "## 市場摘要",
        "",
        "| 市場 | 個股檔數 | 最早至最新日 | 每檔根數中位數 | 少於 252 根 | 覆蓋滿 9 年 | 財報檔覆蓋 | 有標準化公告時間 |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for market in ("US", "TW"):
        group = equities.loc[equities["market"] == market] if not equities.empty else pd.DataFrame()
        if group.empty:
            rows.append(f"| {market} | 0 | — | — | 0 | 0 | 0 | 0 |")
            continue
        first = group["first_date"].replace("", np.nan).dropna().min()
        last = group["last_date"].replace("", np.nan).dropna().max()
        finance_count = int(group["financial_file_present"].fillna(False).astype(bool).sum())
        announcement_count = int((pd.to_numeric(group["announcement_timestamp_rows"], errors="coerce").fillna(0) > 0).sum())
        rows.append(
            f"| {market} | {len(group)} | {first or '—'} – {last or '—'} | {int(group['ohlcv_rows'].median())} | "
            f"{int((group['ohlcv_rows'] < 252).sum())} | {int((group['span_years'] >= 9).sum())} | "
            f"{finance_count}/{len(group)} | {announcement_count}/{len(group)} |"
        )
    rows.extend([
        "",
        "## 判讀與限制",
        "",
        "repo 個股日 K 幾乎都只有約一年，不能把一年的資料稱為 2016–2025 十年樣本。空 CSV、短歷史、週末列、重複日期、OHLC 不一致、極端價格跳動與量能離群均逐檔列入品質表。`weekday_gap_upper_bound` 會把交易所假日也算入可能缺口，因此僅是上界，不稱為精確缺失交易日。",
        "",
        "價格 CSV 沒有 Adjusted Close、股利或拆股事件欄。拆股回溯狀態標為未驗證，股利調整標為否；可能受到公司行動影響的超過 50% 價格跳動會從對應前瞻報酬區間排除並列為品質警告。策略績效因此仍不是完整總報酬。",
        "",
        "財報檔的年度／季度列數僅表示目前快照可取得的期間數。美股標準化季度資料有 period-end，沒有逐筆 filing/acceptance timestamp；台股季資料亦沒有逐筆公告時點。兩者不能作為當時已知的財務事件訊號。資料檔存在不證明該公司在完整歷史期間持續上市；目前 repo 沒有全歷史點時 universe、下市報酬或退市處理。",
        "",
        "只有 `^TWOII` OTC 指數序列可用；本報告僅對有共同交易日的上櫃股票估計相對櫃買指數報酬。美股及台灣上市股沒有可匹配的市場指數序列，超額報酬與 bull/bear/sideways 市場分類不估。",
        "",
        "## 全域品質問題",
        "",
    ])
    if issues.empty:
        rows.append("本次未產生逐檔資料品質旗標。")
    else:
        counts = issues.groupby(["severity", "issue_code"]).size().reset_index(name="count").sort_values(["severity", "count"], ascending=[True, False])
        rows.extend(["| 嚴重度 | 旗標 | 檔數 |", "|---|---|---:|"])
        for item in counts.to_dict(orient="records"):
            rows.append(f"| {item['severity']} | `{item['issue_code']}` | {item['count']} |")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_multiple_testing_report(path: Path, catalog: pd.DataFrame, stats: pd.DataFrame, results: pd.DataFrame, bootstrap_replicates: int) -> None:
    tests = stats.loc[pd.to_numeric(stats.get("p_value_block_bootstrap"), errors="coerce").notna()] if not stats.empty and "p_value_block_bootstrap" in stats else pd.DataFrame()
    q05 = int((pd.to_numeric(tests.get("q_value_fdr"), errors="coerce") < 0.05).sum()) if not tests.empty else 0
    q10 = int((pd.to_numeric(tests.get("q_value_fdr"), errors="coerce") < 0.10).sum()) if not tests.empty else 0
    min_q = float(pd.to_numeric(tests.get("q_value_fdr"), errors="coerce").min()) if not tests.empty else np.nan
    ambiguous = int(results.get("possible_action_or_price_error", pd.Series(dtype=bool)).fillna(False).sum()) if not results.empty else 0
    lines = [
        "# 多重比較與推論紀錄",
        "",
        f"- 預先固定事件定義：{len(catalog)}。",
        f"- 可估的事件 × 市場 × 持有期檢定：{len(tests)}。",
        f"- BH FDR q < 0.05：{q05}；q < 0.10：{q10}；最小 q：{_fmt(min_q, digits=4)}。",
        f"- 事件推論 bootstrap 次數：{bootstrap_replicates}；固定隨機種子以重現結果。",
        f"- 事件結果中有至少一個可能公司行動／價格錯誤區間的訊號：{ambiguous} 筆。該訊號的受影響 horizon 已設為缺值。",
        "",
        "每個市場內，對所有預先列出的 event definition × holding period 的雙尾 bootstrap p 值同時做 Benjamini–Hochberg FDR。沒有依未校正 p 值篩選或修改事件門檻。",
        "",
        "信賴區間與 p 值使用固定種子的循環移動日期區塊 bootstrap。同一日期的跨股票訊號先群集後一起抽樣；區塊長度至少 10 個市場交易日，隨持有期增加，上限 60 日。這會保留同日橫斷面相關和部分相鄰日依賴，但無法解決跨區塊依賴、短樣本或存活者偏誤。60 日 horizon 在約一年資料內只有少數有效區塊，估計精度低。",
        "",
        "事件定義、門檻及排序必須先於查看本次結果固定。結果是條件式探索；統計顯著不等於可交易優勢，也不等於樣本外獲利。完整逐事件數據請執行管線重建；若沒有 `pyarrow`，輸出為 `event-study-results.csv.gz` 而非 Parquet。",
        "",
        "## 最小 q 值事件（僅供探索，不作獲利宣稱）",
        "",
    ]
    if tests.empty:
        lines.append("沒有足夠的完整前瞻觀測可估 p 值。")
    else:
        top = tests.sort_values("q_value_fdr").head(10)
        lines.extend(["| 市場 | 事件 | 持有日 | 有效樣本 | 平均報酬 | 95% 區間 | p | q |", "|---|---|---:|---:|---:|---|---:|---:|"])
        for row in top.to_dict(orient="records"):
            ci = f"{_fmt(row.get('ci95_low'), True)} 至 {_fmt(row.get('ci95_high'), True)}"
            lines.append(f"| {row.get('market')} | `{row.get('event_id')}` | {row.get('holding_sessions')} | {row.get('valid_event_observations')} | {_fmt(row.get('mean_return'), True)} | {ci} | {_fmt(row.get('p_value_block_bootstrap'), digits=4)} | {_fmt(row.get('q_value_fdr'), digits=4)} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html_report(
    path: Path,
    event_stats: pd.DataFrame,
    distribution: pd.DataFrame,
    leaderboard: pd.DataFrame,
    oos: pd.DataFrame,
    issues: pd.DataFrame,
    metadata: dict[str, Any],
) -> None:
    event_records = json.loads(event_stats.to_json(orient="records", date_format="iso")) if not event_stats.empty else []
    dist_markets = sorted(distribution["market"].dropna().astype(str).unique()) if not distribution.empty else []
    dist_families = sorted(distribution["family"].dropna().astype(str).unique()) if not distribution.empty else []
    dist_months = sorted(distribution["month"].dropna().astype(str).unique()) if not distribution.empty else []
    dist_bins = ["<-20%", "-20:-10%", "-10:-5%", "-5:-3%", "-3:0%", "0:3%", "3:5%", "5:10%", "10:20%", ">=20%"]
    if not distribution.empty:
        market_index = {value: index for index, value in enumerate(dist_markets)}
        family_index = {value: index for index, value in enumerate(dist_families)}
        month_index = {value: index for index, value in enumerate(dist_months)}
        bin_index = {value: index for index, value in enumerate(dist_bins)}
        dist_records = [
            [market_index[str(row.market)], family_index[str(row.family)], int(row.holding_sessions), month_index[str(row.month)], bin_index[str(row.return_bin)], int(row.count)]
            for row in distribution.itertuples(index=False)
        ]
    else:
        dist_records = []
    strategy_records = json.loads(leaderboard.to_json(orient="records", date_format="iso")) if not leaderboard.empty else []
    oos_records = json.loads(oos.to_json(orient="records", date_format="iso")) if not oos.empty else []
    issue_counts = issues.groupby(["severity", "issue_code"]).size().reset_index(name="count").to_dict(orient="records") if not issues.empty else []
    payload = {"events": event_records, "distribution": dist_records, "distribution_schema": {"markets": dist_markets, "families": dist_families, "months": dist_months, "bins": dist_bins}, "strategies": strategy_records, "oos": oos_records, "issues": issue_counts, "metadata": metadata}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Research Lab｜研究結果</title>
<style>
:root{{--ink:#152437;--muted:#5f7080;--line:#dce3ea;--paper:#f4f7fa;--card:#fff;--blue:#315ea8;--orange:#bd6624;--green:#25765a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Noto Sans TC",sans-serif}}header{{background:#10283c;color:#fff;padding:24px 18px}}main{{max-width:1120px;margin:auto;padding:16px}}h1{{font-size:1.45rem;margin:0 0 6px}}h2{{font-size:1.12rem;margin:0 0 10px}}p{{margin:.4rem 0}}.muted{{color:var(--muted);font-size:.9rem}}.warning{{border-left:4px solid #d18737;background:#fff8ea;padding:12px 14px;margin:14px 0;border-radius:6px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}}.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}.value{{font-size:1.35rem;font-weight:700}}.filters{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:10px 0}}label{{font-size:.85rem;color:var(--muted)}}select,input{{width:100%;padding:9px;border:1px solid var(--line);border-radius:6px;background:white;color:var(--ink);margin-top:4px}}.table-wrap{{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:8px;background:white}}table{{border-collapse:collapse;width:100%;font-size:.87rem}}th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{position:sticky;top:0;background:#edf2f7;z-index:1}}.bars{{display:flex;align-items:end;gap:4px;height:190px;padding:12px 4px 28px;overflow-x:auto}}.bar{{min-width:38px;background:var(--blue);border-radius:5px 5px 0 0;position:relative}}.bar span{{position:absolute;bottom:-25px;left:50%;transform:translateX(-50%);font-size:10px;color:var(--muted);white-space:nowrap}}.bar em{{position:absolute;top:-20px;left:50%;transform:translateX(-50%);font-size:10px;font-style:normal}}.section{{margin:18px 0}}.pill{{display:inline-block;padding:2px 8px;border-radius:99px;background:#edf2f7;font-size:.82rem}}footer{{color:var(--muted);padding:20px 0;font-size:.85rem}}@media(max-width:620px){{main{{padding:10px}}header{{padding:18px 12px}}th,td{{padding:7px;font-size:.8rem}}.table-wrap{{max-height:440px}}}}
</style></head><body>
<header><h1>Market Research Lab｜市場事件與策略探索</h1><div id="runline" class="muted" style="color:#c8d7e5"></div></header>
<main>
<div class="warning"><b>研究限制：</b>目前個股價量快照約一年，股票行動調整與歷史成分股名單未驗證。末段測試日期已在開發期間檢視，以下不稱為 untouched holdout。事件結果是條件式探索；短樣本策略排名不是已確認獲利。</div>
<section class="grid" id="kpis"></section>
<section class="card section"><h2>事件研究篩選</h2><p class="muted">訊號於收盤後觀察，假設在個股下一個可用日 K 開盤進場；報酬分布依月份彙總，可篩選市場、事件家族、持有日及日期範圍。停牌與逐市場日曆無法驗證。</p>
<div class="filters"><label>市場<select id="market"></select></label><label>事件家族<select id="family"></select></label><label>事件定義<select id="event"></select></label><label>持有日<select id="holding"></select></label><label>日期起<input id="from" type="date"></label><label>日期迄<input id="to" type="date"></label></div><p class="muted">日期區間用於月報酬分布；事件統計表中的推論區間是固定全樣本估計，不會因日期篩選重新檢定。</p>
<h2>事件後報酬分布</h2><div class="bars" id="bars"></div><p class="muted" id="chart-caption"></p>
<div class="table-wrap"><table><thead><tr><th>事件</th><th>有效樣本</th><th>股票／指數</th><th>平均</th><th>中位數</th><th>正報酬率</th><th>>5%</th><th><−5%</th><th>95% 區間</th><th>FDR q</th></tr></thead><tbody id="events"></tbody></table></div></section>
<section class="card section"><h2>策略候選與樣本外</h2><p class="muted">候選只依訓練區 Sharpe 排序；最終測試未用於調參。成本為固定情境，非實際券商費用。</p><div class="filters"><label>策略市場<select id="strategy-market"></select></label></div><div class="table-wrap"><table><thead><tr><th>ID</th><th>策略</th><th>持有日</th><th>訓練 Sharpe</th><th>訓練筆數</th><th>驗證報酬</th><th>測試報酬</th><th>測試筆數</th><th>測試狀態</th></tr></thead><tbody id="strategies"></tbody></table></div><p id="oos-note" class="muted"></p></section>
<section class="card section"><h2>資料品質警告</h2><div class="table-wrap"><table><thead><tr><th>嚴重度</th><th>問題</th><th>檔數</th></tr></thead><tbody id="issues"></tbody></table></div></section>
<footer>研究日期與 repo SHA 請見資料覆蓋報告。所有統計區間、群集方式與公司行動限制見 <a href="methodology.md">methodology.md</a> 及 <a href="multiple-testing-report.md">multiple-testing-report.md</a>。</footer>
</main><script id="payload" type="application/json">{data}</script>
<script>
const D=JSON.parse(document.getElementById('payload').textContent), E=D.events, H=D.distribution, DS=D.distribution_schema, S=D.strategies;
const meta=D.metadata||{{}};document.getElementById('runline').textContent=`執行日 ${{meta.generated_at||''}} ｜ main ${{meta.repo_sha||''}} ｜ ${{meta.event_definition_count||0}} 個事件定義`;
const kpi=[['價量檔案',meta.price_file_count||0],['研究個股',meta.equity_count||0],['事件定義',meta.event_definition_count||0],['候選策略',meta.strategy_candidate_count||0],['末段測試策略',meta.oos_strategy_count||0],['測試通過',meta.oos_pass_count||0],['末段資料狀態',meta.holdout_status||'unknown']];
document.getElementById('kpis').innerHTML=kpi.map(x=>`<div class="card"><div class="muted">${{x[0]}}</div><div class="value">${{x[1]}}</div></div>`).join('');
function opts(id,vals,all='全部'){{const el=document.getElementById(id);el.innerHTML=`<option value="">${{all}}</option>`+vals.map(v=>`<option value="${{v}}">${{v}}</option>`).join('')}}
opts('market',[...new Set(E.map(x=>x.market))]);opts('strategy-market',[...new Set(S.map(x=>x.market))]);opts('family',[...new Set(E.map(x=>x.family))]);opts('event',[...new Set(E.map(x=>x.event_id))]);opts('holding',[...new Set(E.map(x=>x.holding_sessions))].sort((a,b)=>a-b));
function selectedRows(){{const m=document.getElementById('market').value,f=document.getElementById('family').value,e=document.getElementById('event').value,h=document.getElementById('holding').value,from=document.getElementById('from').value,to=document.getElementById('to').value;return E.filter(x=>(!m||x.market===m)&&(!f||x.family===f)&&(!e||x.event_id===e)&&(!h||String(x.holding_sessions)===h)&&(!from||!x.sample_end||x.sample_end>=from)&&(!to||!x.sample_start||x.sample_start<=to))}}
function pct(x){{return x==null||!Number.isFinite(+x)?'—':(+x*100).toFixed(2)+'%'}}function num(x){{return x==null||!Number.isFinite(+x)?'—':(+x).toFixed(3)}}
function renderEvents(){{const rows=selectedRows();document.getElementById('events').innerHTML=rows.slice(0,500).map(x=>`<tr><td><span class="pill">${{x.family}}</span> ${{x.event_id}}</td><td>${{x.valid_event_observations||0}}</td><td>${{x.unique_equities||0}} / ${{x.unique_indexes||0}}</td><td>${{pct(x.mean_return)}}</td><td>${{pct(x.median_return)}}</td><td>${{pct(x.positive_probability)}}</td><td>${{pct(x.p_return_gt_5pct)}}</td><td>${{pct(x.p_return_lt_minus_5pct)}}</td><td>${{pct(x.ci95_low)}} – ${{pct(x.ci95_high)}}</td><td>${{num(x.q_value_fdr)}}</td></tr>`).join('')||'<tr><td colspan="10">沒有完整前瞻樣本。</td></tr>';renderBars(rows)}}
function renderBars(rows){{const pick=rows[0];const root=document.getElementById('bars');if(!pick){{root.innerHTML='';return}}const from=document.getElementById('from').value,to=document.getElementById('to').value;const bins=DS.bins,counts=Object.fromEntries(bins.map(x=>[x,0]));const mi=DS.markets.indexOf(pick.market),fi=DS.families.indexOf(pick.family);H.filter(x=>x[0]===mi&&x[1]===fi&&+x[2]===+pick.holding_sessions&&(!from||DS.months[x[3]]+'-31'>=from)&&(!to||DS.months[x[3]]+'-01'<=to)).forEach(x=>counts[bins[x[4]]]+=x[5]);const max=Math.max(1,...Object.values(counts));root.innerHTML=bins.map(b=>`<div class="bar" style="height:${{Math.max(3,150*counts[b]/max)}}px"><em>${{counts[b]}}</em><span>${{b}}</span></div>`).join('');document.getElementById('chart-caption').textContent=`${{pick.market}}｜${{pick.family}} 事件家族｜${{pick.holding_sessions}} 個交易日；直方圖為家族內事件月彙總，依日期欄位篩選。`}}
function renderStrategies(){{const m=document.getElementById('strategy-market').value;const rows=S.filter(x=>!m||x.market===m).sort((a,b)=>(a.training_rank||999)-(b.training_rank||999));document.getElementById('strategies').innerHTML=rows.slice(0,200).map(x=>`<tr><td>${{x.strategy_id}}</td><td>${{x.candidate_key}}</td><td>${{x.holding_sessions}}</td><td>${{num(x.train_sharpe_ratio)}}</td><td>${{x.train_trade_count||0}}</td><td>${{pct(x.validation_total_return)}}</td><td>${{pct(x.test_total_return)}}</td><td>${{x.test_trade_count||0}}</td><td>${{x.selected_from_training?(meta.holdout_status==='exposed_test_window'?'已暴露末段資料':'末段測試候選'):'訓練區排名'}}</td></tr>`).join('')||'<tr><td colspan="9">沒有可排序的策略結果。</td></tr>';const tested=(D.oos||[]).filter(x=>!m||x.market===m);const passed=tested.filter(x=>x.oos_pass).length;const costs=meta.strategy_cost_scenarios_bps_by_market||{{}},costText=Object.entries(costs).filter(([k])=>!m||k===m).map(([k,v])=>`${{k}} ${{v.join('/')}} bps`).join('；');document.getElementById('oos-note').textContent=`末段測試情境列 ${{tested.length}} 列（來回成本：${{costText}}）；其中 ${{passed}} 列符合形式門檻。此末段已暴露；另受測試期短於三年、公司行動及退市資料不完整限制。`}}
document.getElementById('issues').innerHTML=D.issues.map(x=>`<tr><td>${{x.severity}}</td><td>${{x.issue_code}}</td><td>${{x.count}}</td></tr>`).join('')||'<tr><td colspan="3">無品質警告</td></tr>';
['market','family','event','holding','from','to'].forEach(id=>document.getElementById(id).addEventListener('change',renderEvents));document.getElementById('strategy-market').addEventListener('change',renderStrategies);renderEvents();renderStrategies();
</script></body></html>'''
    path.write_text(page, encoding="utf-8")

