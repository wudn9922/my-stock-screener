#!/usr/bin/env python3
"""Local-only swing review images/CSV; uses production trace, never writes docs/report.

Requires matplotlib in addition to normal pandas/numpy dependencies.
Example: python scripts/preview_swing_breakouts.py --out-dir /tmp/atlas-swing-review
"""
import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from build_breadth import load_daily_bars, present_matrix, select_tw_symbols, select_us_symbols, trading_days
from build_breakouts import PRICE_COLUMNS, REPO_ROOT, build_market, clean_bars
from build_full_market_breakouts import ACTIVE_STRATEGIES, params_meta
from swing_breakouts import ATR_PERIOD, REVERSAL_ATR, swing_high_trace


TITLES = {
    '3230.TWO': '橫盤小高點不取代八月的波段前高',
    '3287.TWO': '保留九月長上影線高點，直到真正突破',
    'VEEV': '移除九月小轉折，等新一段上升與回落',
    '2305.TW': '保留原本判對的 72.50 突破',
}
REMOVED = {'3230.TWO': '2026-10-01', 'VEEV': '2026-09-24'}


def local_frames(data_dir):
    names = [p.name for p in data_dir.glob('*.csv')]
    members = {'tw': select_tw_symbols(names), 'us': select_us_symbols(names)[0]}
    out = {}
    for market, symbols in members.items():
        frames = {}
        for symbol in symbols:
            frame = load_daily_bars(data_dir / f'{symbol}.csv', PRICE_COLUMNS)
            if frame is not None:
                frame = frame[frame.index.dayofweek < 5]
                if not frame.empty:
                    frames[symbol] = frame
        out[market] = frames
    return out


def draw(symbol, bars, calendar, since, out_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.dates as md
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Rectangle

    plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    trace = swing_high_trace(bars)
    view = bars.loc[since:]
    first, last = view.index[0], view.index[-1]
    fig, (ax, summary) = plt.subplots(2, 1, figsize=(13, 7.5), gridspec_kw={'height_ratios': [5, 1.1]}, layout='constrained')
    fig.patch.set_facecolor('#fcfcfd')
    ax.set_facecolor('#fcfcfd')
    for day, row in view.iterrows():
        x = md.date2num(day)
        o, h, l, c = (float(row[k]) for k in ('Open', 'High', 'Low', 'Close'))
        color = '#d34c56' if c >= o else '#13836c'
        ax.vlines(x, l, h, color=color, lw=1.15, zorder=2)
        ax.add_patch(Rectangle((x - .32, min(o, c)), .64, max(abs(c-o), c*.0008), facecolor=color, edgecolor=color, zorder=3))
    ax.step(view.index, trace.levels.loc[view.index], where='post', color='#2859a4', lw=1.7, linestyle='--', zorder=1)
    # Preserve elapsed time and expose missing stock candles using the market calendar.
    missing = calendar[(calendar >= first) & (calendar <= last)].difference(bars.index)
    if len(missing):
        positions = [calendar.get_loc(day) for day in missing]
        groups = np.split(np.arange(len(missing)), np.flatnonzero(np.diff(positions) > 1) + 1)
        for group in groups:
            start, end = missing[group[0]], missing[group[-1]]
            ax.axvspan(md.date2num(start)-.5, md.date2num(end)+.5, facecolor='#d8dce2', alpha=.65, zorder=0)
            if len(group) >= 3:
                ax.text(md.date2num(start)+(md.date2num(end)-md.date2num(start))/2, .5,
                        f'此檔缺 {len(group)} 根市場日K\n{start:%m-%d}～{end:%m-%d}\n不補價、不提前確認',
                        transform=ax.get_xaxis_transform(), ha='center', fontsize=9, color='#5b6575')
    rows = []
    for pivot in trace.highs:
        peak, confirm = bars.index[pivot.pivot_index], bars.index[pivot.confirmed_index]
        if peak < first or peak > last:
            continue
        ax.plot([peak, confirm], [pivot.price]*2, color='#a8b4c8', lw=1, linestyle=':')
        ax.scatter([peak], [pivot.price], s=42, color='#2859a4', marker='D', zorder=5)
        ax.scatter([confirm], [pivot.price], s=34, facecolors='white', edgecolors='#2859a4', marker='s', zorder=5)
        ax.annotate(f'{peak:%m-%d}\n{pivot.price:.2f}', (peak, pivot.price), xytext=(0, 12), textcoords='offset points', ha='center', fontsize=9, color='#244a84')
        hits = trace.fresh & (trace.pivot_indices == pivot.pivot_index)
        entry_days = bars.index[hits]
        if len(entry_days):
            day = entry_days[-1]
            entry = max(float(bars.loc[day, 'Open']), pivot.price)
            ret = (float(bars['Close'].iloc[-1])/entry-1)*100
            result = '、'.join(f'{attempt:%m-%d}' for attempt in entry_days) + f'／末次 {entry:.2f}／{ret:+.2f}%'
            for attempt in entry_days:
                if attempt >= first:
                    attempt_entry = max(float(bars.loc[attempt, 'Open']), pivot.price)
                    ax.scatter([attempt], [attempt_entry], marker='^', s=95, color='#d58924', edgecolor='white', lw=.8, zorder=6)
        else:
            result = '已由下一波段取代（未觸發）' if any(p.confirmed_index > pivot.confirmed_index for p in trace.highs) else '截至末日未突破'
        rows.append([f'{peak:%Y-%m-%d}', f'{pivot.price:.2f}', f'{confirm:%Y-%m-%d}', result])
    removed = pd.Timestamp(REMOVED[symbol]) if symbol in REMOVED else None
    if removed is not None and removed in view.index:
        high = float(view.loc[removed, 'High'])
        ax.scatter([removed], [high], marker='x', s=60, color='#596579', zorder=6)
        ax.annotate(f'{removed:%m-%d} 舊訊號已移除', (removed, high), xytext=(0,-30), textcoords='offset points', ha='center', fontsize=9, color='#596579')
    ax.set_title(f'{symbol}｜{TITLES.get(symbol, "波段前高檢視")}\n'
                 f'試算規則：收盤反向移動 ≥ {REVERSAL_ATR:g} × ATR({ATR_PERIOD})；確認後次日生效。紅漲、綠跌。',
                 loc='left', fontsize=14, weight='bold', pad=14)
    ax.set_ylabel('價格')
    ax.grid(axis='y', color='#e0e4ea', lw=.7)
    ax.xaxis.set_major_locator(md.WeekdayLocator(byweekday=md.MO, interval=2))
    ax.xaxis.set_major_formatter(md.DateFormatter('%m-%d'))
    ax.set_xlim(md.date2num(first)-2, md.date2num(last)+3)
    ax.margins(y=.18)
    ax.legend(handles=[Line2D([], [], color='#2859a4', linestyle='--', label='已確認波段高點參考線'),
                       Line2D([], [], marker='D', color='#2859a4', linestyle='None', label='實際波段高點'),
                       Line2D([], [], marker='s', markerfacecolor='white', color='#2859a4', linestyle='None', label='收盤確認日'),
                       Line2D([], [], marker='^', color='#d58924', linestyle='None', label='突破嘗試／理論進場'),
                       Patch(facecolor='#d8dce2', alpha=.65, label='缺漏市場日K')], loc='upper left', ncol=3, fontsize=8)
    summary.axis('off')
    if rows:
        table = summary.table(cellText=rows, colLabels=['高點實際日期', '高點價', '收盤確認日期', '突破嘗試／末次理論進場／末日報酬'],
                              colWidths=[.2,.12,.2,.48], loc='center', cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1, 1.5)
        for (r, _), cell in table.get_celld().items():
            cell.set_edgecolor('#d9dfe8')
            if r == 0:
                cell.set_facecolor('#e9eef7')
    fig.text(.99,.002,f'專案 data/{symbol}.csv；資料截至 {last:%Y-%m-%d}；日線理論成交，未計滑價與費用', ha='right', fontsize=8, color='#697386')
    out = out_dir / f'{symbol.replace(".", "-")}.png'
    fig.savefig(out, dpi=150)
    plt.close(fig)
    # Audit trail includes real pivot/confirmation dates, not guessed bar offsets.
    csv_path = out.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['date','known_before_open','reference','actual_pivot_date','confirmed_at_close','signal','entry'])
        for i, day in enumerate(bars.index):
            if day < first: continue
            p, c = int(trace.pivot_indices.iloc[i]), int(trace.confirmed_indices.iloc[i])
            writer.writerow([day.date(), bool(trace.known.iloc[i]), trace.levels.iloc[i] if p>=0 else '',
                             bars.index[p].date() if p>=0 else '', bars.index[c].date() if c>=0 else '',
                             bool(trace.fresh.iloc[i]), max(float(bars['Open'].iloc[i]), trace.levels.iloc[i]) if trace.fresh.iloc[i] else ''])
    print(out)
    for row in rows: print(symbol, *row, sep=' | ')


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', nargs='+', default=list(TITLES))
    parser.add_argument('--since', default='2026-08-01')
    parser.add_argument('--data-dir', type=Path, default=Path(REPO_ROOT)/'data')
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--stats', action='store_true', help='Also compute local subset window statistics (not full market)')
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    inputs = local_frames(args.data_dir)
    for symbol in args.symbols:
        market = 'tw' if symbol.endswith(('.TW','.TWO')) else 'us'
        frames = inputs[market]
        raw = frames[symbol]
        bars, _ = clean_bars(raw, raw.index[-1])
        calendar = trading_days(present_matrix(frames), bars.index[-1])
        draw(symbol, bars, calendar, pd.Timestamp(args.since), args.out_dir)
    if args.stats:
        markets = {}
        for market, frames in inputs.items():
            result = build_market(frames, '本機 data/ 個股樣本（非全市場）', market)
            result['scope'] = 'local-preview'
            for window in result['windows'].values():
                window['strategies'] = {key:window['strategies'][key] for key in ACTIVE_STRATEGIES}
            markets[market] = result
            print(market, result['asOf'], 'evaluated', result['evaluated'], '/', result['universeCount'])
            for key, window in result['windows'].items(): print(key, window['strategies']['swingHigh'])
        payload = {'params':params_meta(), 'markets':markets}
        (args.out_dir/'local-stats.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
