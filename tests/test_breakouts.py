"""突破買進法勝率（scripts/build_breakouts.py）的離線測試：合成 K 線，不連網路、不 import yfinance。"""

import contextlib
import io
import json
import os
import random
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_breakouts as bo  # noqa: E402

AS_OF = "2026-10-09"  # 週五


def weekdays_ending(last, count):
    day = date.fromisoformat(last)
    out = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return list(reversed(out))


def make_bars(closes, last=AS_OF, highs=None, lows=None, volumes=None, dates=None):
    """與 data/*.csv 同欄位（High/Low/Close/Volume）的 DataFrame；預設 High = Low = Close、量 1000。"""
    dates = pd.DatetimeIndex(dates if dates is not None else weekdays_ending(last, len(closes)))
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "High": [float(h) for h in highs] if highs is not None else closes,
            "Low": [float(v) for v in lows] if lows is not None else closes,
            "Close": closes,
            "Volume": [float(v) for v in volumes] if volumes is not None else [1000.0] * len(closes),
        },
        index=dates,
    )


def flat_then(tail, base=100.0, length=60):
    """長度 length 的平盤（收盤 = base），最後幾根換成 tail。"""
    return [base] * (length - len(tail)) + list(tail)


def filler(count=5, length=60):
    """讓 asOf 眾數與交易日曆穩定的平盤股票。"""
    return {f"F{i}": make_bars([50.0] * length) for i in range(count)}


def stats(market, strategy, window="1m"):
    return market["windows"][window]["strategies"][strategy]


class StrategySignalTest(unittest.TestCase):
    def fresh(self, bars, key="high20", width=0.14):
        clean, _ = bo.clean_bars(bars, bars.index[-1])
        fresh, level, known = bo.strategy_signals(clean, width)[key]
        return clean, fresh, level, known

    def test_close_equal_to_prior_high_is_not_a_breakout(self):
        _, fresh, _, _ = self.fresh(make_bars(flat_then([100.0])))
        self.assertFalse(fresh.iloc[-1])
        _, fresh, level, _ = self.fresh(make_bars(flat_then([100.01])))
        self.assertTrue(fresh.iloc[-1])
        self.assertEqual(level.iloc[-1], 100.0)

    def test_reference_high_excludes_the_breakout_day_itself(self):
        # 當日盤中高點 130 遠高於收盤，但參考價只看前 20 根
        closes = flat_then([101.0])
        highs = flat_then([130.0])
        _, fresh, level, _ = self.fresh(make_bars(closes, highs=highs))
        self.assertTrue(fresh.iloc[-1])
        self.assertEqual(level.iloc[-1], 100.0)

    def test_reference_uses_highs_not_closes_and_only_the_last_n_bars(self):
        closes = [100.0] * 60
        highs = [100.0] * 60
        highs[-22] = 120.0  # 當日往前第 21 根：不在 20 日回看內
        highs[-5] = 104.0  # 盤中高點擋住突破
        closes[-1] = 103.0
        _, fresh, level, _ = self.fresh(make_bars(closes, highs=highs))
        self.assertFalse(fresh.iloc[-1])
        self.assertEqual(level.iloc[-1], 104.0)
        closes[-1] = 104.5
        _, fresh, _, _ = self.fresh(make_bars(closes, highs=highs))
        self.assertTrue(fresh.iloc[-1])
        # 往前第 20 根仍在回看內
        highs[-21] = 120.0
        _, fresh, level, _ = self.fresh(make_bars(closes, highs=highs))
        self.assertFalse(fresh.iloc[-1])
        self.assertEqual(level.iloc[-1], 120.0)

    def test_consecutive_new_highs_are_one_wave(self):
        closes = flat_then([101, 102, 103, 102.5, 104])
        clean, fresh, _, _ = self.fresh(make_bars(closes))
        self.assertEqual(list(fresh.iloc[-5:]), [True, False, False, False, False])

    def test_new_wave_after_cooldown_counts_again(self):
        # 第一波後休息 COOLDOWN_DAYS 根都沒有突破，再創高才是新突破
        quiet = [100.5] * bo.COOLDOWN_DAYS
        closes = flat_then([101] + quiet + [101.5], length=70)
        _, fresh, _, _ = self.fresh(make_bars(closes))
        self.assertTrue(fresh.iloc[-1])
        self.assertTrue(fresh.iloc[-(bo.COOLDOWN_DAYS + 2)])
        # 少休息一天就還是同一波
        closes = flat_then([101] + quiet[:-1] + [101.5], length=70)
        _, fresh, _, _ = self.fresh(make_bars(closes))
        self.assertFalse(fresh.iloc[-1])

    def test_needs_lookback_plus_cooldown_bars(self):
        need = 20 + bo.COOLDOWN_DAYS
        _, fresh, _, known = self.fresh(make_bars([100.0] * need + [101.0]))
        self.assertTrue(known.iloc[-1])
        self.assertTrue(fresh.iloc[-1])
        _, fresh, _, known = self.fresh(make_bars([100.0] * (need - 1) + [101.0]))
        self.assertFalse(known.iloc[-1])
        self.assertFalse(fresh.iloc[-1])

    def test_box_width_threshold_per_market(self):
        # 前 20 根在 100～112 之間震盪：寬度 12%，台股門檻 14% 算箱型、美股 10% 不算
        tail_highs = [112.0 if i % 2 else 100.0 for i in range(20)]
        closes = [100.0] * 40 + tail_highs + [113.0]
        highs = list(closes)
        bars = make_bars(closes, highs=highs)
        _, tw_box, level, _ = self.fresh(bars, "box20", bo.BOX_MAX_WIDTH["tw"])
        _, us_box, _, _ = self.fresh(bars, "box20", bo.BOX_MAX_WIDTH["us"])
        _, us_high, _, _ = self.fresh(bars, "high20", bo.BOX_MAX_WIDTH["us"])
        self.assertEqual(level.iloc[-1], 112.0)
        self.assertTrue(tw_box.iloc[-1])
        self.assertFalse(us_box.iloc[-1])
        self.assertTrue(us_high.iloc[-1])

    def test_box_uses_intraday_lows_and_highs(self):
        closes = [100.0] * 60 + [101.0]
        lows = [100.0] * 60 + [101.0]
        lows[-3] = 85.0  # 箱底被盤中低點拉到 85：(100−85)/85 = 17.6% > 14%
        _, box, _, _ = self.fresh(make_bars(closes, lows=lows), "box20")
        self.assertFalse(box.iloc[-1])

    def test_volume_confirmation_uses_prior_average_only(self):
        volumes = [1000.0] * 60
        volumes[-1] = 1500.0  # 剛好 1.5 倍
        bars = make_bars(flat_then([101.0]), volumes=volumes)
        _, fresh, _, _ = self.fresh(bars, "high20Vol")
        self.assertTrue(fresh.iloc[-1])
        volumes[-1] = 1499.0
        _, fresh, _, _ = self.fresh(make_bars(flat_then([101.0]), volumes=volumes), "high20Vol")
        self.assertFalse(fresh.iloc[-1])


class CleanBarsTest(unittest.TestCase):
    def test_zero_volume_days_are_dropped_not_filled(self):
        closes = flat_then([130.0, 101.0])
        volumes = [1000.0] * 58 + [0.0, 1000.0]
        clean, anomalies = bo.clean_bars(make_bars(closes, volumes=volumes), pd.Timestamp(AS_OF))
        self.assertEqual(len(clean), 59)
        self.assertEqual(anomalies, [])
        self.assertNotIn(130.0, clean["High"].tolist())

    def test_high_low_widened_to_include_close(self):
        bars = make_bars([10.0, 11.0], highs=[10.0, 10.5], lows=[9.0, 11.5])
        clean, _ = bo.clean_bars(bars, bars.index[-1])
        self.assertEqual(clean["High"].tolist(), [10.0, 11.0])
        self.assertEqual(clean["Low"].tolist(), [9.0, 11.0])

    def test_jump_over_limit_cuts_history_at_the_last_anomaly(self):
        closes = [100.0] * 30 + [40.0] * 20 + [100.0] * 10  # -60%、+150%
        clean, anomalies = bo.clean_bars(make_bars(closes), pd.Timestamp(AS_OF))
        self.assertEqual([round(change, 2) for _, change in anomalies], [-0.6, 1.5])
        self.assertEqual(len(clean), 10)
        self.assertEqual(clean.index[0], anomalies[-1][0])

    def test_jump_at_exactly_the_limit_is_kept(self):
        clean, anomalies = bo.clean_bars(make_bars([100.0] * 10 + [130.0]), pd.Timestamp(AS_OF))
        self.assertEqual(anomalies, [])
        self.assertEqual(len(clean), 11)

    def test_bars_after_as_of_are_ignored(self):
        bars = make_bars([100.0] * 10 + [10.0], last="2026-10-12")
        clean, anomalies = bo.clean_bars(bars, pd.Timestamp(AS_OF))
        self.assertEqual(clean.index[-1], pd.Timestamp(AS_OF))
        self.assertEqual(anomalies, [])


class BuildMarketTest(unittest.TestCase):
    def build(self, frames, market="tw", **kwargs):
        return bo.build_market({**filler(), **frames}, "測試", market, **kwargs)

    def test_maturity_counts_only_breakouts_held_at_least_k_days(self):
        frames = {}
        for hold in range(0, 6):
            # 突破日在 asOf 前 hold 個交易日，之後收盤 +1
            tail = [101.0] + [102.0] * hold
            frames[f"H{hold}"] = make_bars(flat_then(tail))
        market = self.build(frames)
        week = stats(market, "high20", "1w")
        self.assertEqual(week["n"], 5)  # hold 0..4 在近 5 日內
        self.assertEqual(week["maturedN"], 2)  # hold 3、4
        self.assertEqual(week["avgHoldDays"], 3.5)
        month = stats(market, "high20")
        self.assertEqual(month["n"], 6)
        self.assertEqual(month["maturedN"], 3)
        self.assertEqual(month["winRate"], 100.0)
        self.assertEqual(market["windows"]["1w"]["maturedThrough"], weekdays_ending(AS_OF, 4)[0].isoformat())

    def test_win_loss_tie_and_failure(self):
        frames = {
            "WIN": make_bars(flat_then([101, 103, 103, 103])),
            "TIE": make_bars(flat_then([101, 101, 101, 101])),  # 報酬 0：不算勝、也沒跌破前高
            "LOSS": make_bars(flat_then([101, 100.5, 100.5, 100.5])),  # 跌但仍在前高 100 之上
            "FAIL": make_bars(flat_then([101, 99, 99, 99])),  # 跌回前高之下
        }
        market = self.build(frames)
        result = stats(market, "high20")
        self.assertEqual(result["maturedN"], 4)
        self.assertEqual(result["winRate"], 25.0)
        self.assertEqual(result["failRate"], 25.0)
        returns = sorted([103 / 101 - 1, 0.0, 100.5 / 101 - 1, 99 / 101 - 1])
        self.assertAlmostEqual(result["avgReturn"], round(float(np.mean(returns)) * 100, 2))
        self.assertAlmostEqual(result["medianReturn"], round(float(np.median(returns)) * 100, 2))

    def test_window_boundaries_and_first_breakout_only(self):
        days = weekdays_ending(AS_OF, 60)
        # 6 個交易日前突破：不在近 1 週、在近 1 月
        old = make_bars(flat_then([101, 101, 101, 101, 101, 101]))
        # 窗口內兩波：只計第一次（第二波距離第一波 > COOLDOWN）
        two_waves = make_bars(flat_then([101] + [100.5] * bo.COOLDOWN_DAYS + [102, 102, 102, 102], length=60))
        market = self.build({"OLD": old, "TWO": two_waves})
        self.assertEqual(market["windows"]["1w"]["start"], days[-5].isoformat())
        self.assertEqual(market["windows"]["1m"]["start"], days[-21].isoformat())
        # 近 1 週只看得到 TWO 的第二波（3 日前）；OLD（5 日前）在窗口外
        week = stats(market, "high20", "1w")
        self.assertEqual((week["n"], week["avgHoldDays"]), (1, 3.0))
        # 近 1 月：OLD 一筆、TWO 只取窗口內第一次（14 日前那波），第二波不重複計
        month = stats(market, "high20")
        self.assertEqual((month["n"], month["avgHoldDays"]), (2, (5 + 14) / 2))

    def test_continuation_of_a_wave_started_before_the_window_is_not_counted(self):
        # 突破發生在近 1 週窗口開始前 2 天，之後每天續創新高
        closes = flat_then([101, 102, 103, 104, 105, 106, 107])
        market = self.build({"RUN": make_bars(closes)})
        self.assertEqual(stats(market, "high20", "1w")["n"], 0)
        self.assertEqual(stats(market, "high20")["n"], 1)

    def test_base_rate_matches_entry_days_of_the_breakouts(self):
        # 近 1 週：asOf 前 4 日（窗口第一天）突破。其他股票從該日起：3 漲 2 平（filler）+ 突破股自己
        frames = {
            "BRK": make_bars(flat_then([101, 101, 101, 101, 102])),
            "UP1": make_bars([20.0] * 56 + [20, 21, 21, 21]),
            "UP2": make_bars([20.0] * 56 + [20, 20, 20, 22]),
            "DN1": make_bars([20.0] * 56 + [20, 19, 19, 19]),
        }
        market = bo.build_market({**filler(2), **frames}, "測試", "tw")
        # asOf 前 4 日：UP1/UP2 該日收 20 → asOf 21/22 上漲；DN1 下跌；F0/F1 平盤；BRK 上漲
        result = stats(market, "high20", "1w")
        self.assertEqual(result["maturedN"], 1)
        self.assertEqual(result["baseRate"], round(3 / 6 * 100, 1))
        expected_mean = np.mean([102 / 101 - 1, 0.05, 0.10, -0.05, 0.0, 0.0])
        self.assertEqual(result["baseAvgReturn"], round(float(expected_mean) * 100, 2))

    def test_window_market_base_is_equal_weight_over_matured_entry_days(self):
        frames = {"UP": make_bars([10.0 + i for i in range(60)])}
        market = bo.build_market({**filler(1), **frames}, "測試", "tw")
        base = market["windows"]["1w"]["market"]
        self.assertEqual(base["entryDays"], 2)
        self.assertEqual(base["pairs"], 4)
        self.assertEqual(base["upRate"], 50.0)

    def test_missing_days_are_not_filled_and_lookback_counts_own_bars(self):
        days = weekdays_ending(AS_OF, 61)
        # 刪掉一天（缺日）：20 日回看往前多涵蓋一個日曆日，最舊那天的高點 110 就落在回看內
        keep = [day for i, day in enumerate(days) if i != 50]
        closes = [100.0] * len(keep)
        highs = [100.0] * len(keep)
        highs[-21] = 110.0
        closes[-1] = 105.0
        market = self.build({"GAP": make_bars(closes, highs=highs, dates=keep)})
        self.assertEqual(stats(market, "high20")["n"], 0)

    def test_zero_volume_breakout_day_is_not_an_event(self):
        volumes = [1000.0] * 56 + [0.0, 1000.0, 1000.0, 1000.0]
        market = self.build({"ZV": make_bars(flat_then([120.0, 100, 100, 100]), volumes=volumes)})
        self.assertEqual(stats(market, "high20")["n"], 0)

    def test_split_like_jump_excludes_the_stock_until_history_rebuilds(self):
        closes = [200.0] * 50 + [100.0] * 9 + [101.0]  # -50% 疑似分割，之後只剩 10 根
        market = self.build({"SPLIT": make_bars(closes)})
        self.assertEqual(stats(market, "high20")["n"], 0)
        self.assertEqual(market["anomalies"]["stocks"], 1)
        self.assertEqual(market["anomalies"]["recent"][0]["symbol"], "SPLIT")
        self.assertEqual(market["anomalies"]["recent"][0]["changePct"], -50.0)
        # 沒有切斷時，跳空前的前高 200 會讓這檔永遠不算突破；切斷後也不會被誤算成突破
        self.assertEqual(stats(market, "high20")["eligible"], 5)

    def test_reverse_split_jump_does_not_create_a_false_breakout(self):
        closes = [50.0] * 50 + [100.0] * 10  # +100%：沒有處理會被當成突破前高
        market = self.build({"REV": make_bars(closes)})
        self.assertEqual(stats(market, "high20")["n"], 0)

    def test_weekend_bars_are_ignored_and_counted(self):
        days = weekdays_ending(AS_OF, 60)
        bars = make_bars(flat_then([100.0]))
        # 週日假K：高點 = 漲停價，沒有排除會擋住後面的突破
        fake = pd.DataFrame({"High": [110.0], "Low": [90.0], "Close": [100.0], "Volume": [10.0]},
                            index=pd.DatetimeIndex([days[-3] - timedelta(days=days[-3].weekday() + 1)]))
        bars = pd.concat([bars, fake]).sort_index()
        bars.loc[bars.index[-1], ["High", "Close"]] = 101.0
        market = self.build({"WK": bars})
        self.assertEqual(market["weekendBars"], 1)
        self.assertEqual(stats(market, "high20", "1w")["n"], 1)

    def test_stale_stocks_are_excluded_and_counted(self):
        stale = make_bars(flat_then([101.0]), last="2026-10-08")
        zero_on_as_of = make_bars(flat_then([101.0]), volumes=[1000.0] * 59 + [0.0])
        market = self.build({"STALE": stale, "ZERO": zero_on_as_of})
        self.assertEqual(market["asOf"], AS_OF)
        self.assertEqual(market["universeCount"], 7)
        self.assertEqual(market["evaluated"], 5)
        self.assertEqual(market["stale"], 2)
        self.assertEqual(stats(market, "high20")["n"], 0)

    def test_empty_window_gives_null_stats_not_zero(self):
        market = self.build({})
        result = stats(market, "box20Vol")
        self.assertEqual(result["n"], 0)
        self.assertIsNone(result["winRate"])
        self.assertIsNone(result["baseRate"])
        self.assertEqual(result["eligible"], 5)

    def test_short_history_is_not_eligible_for_long_lookbacks(self):
        market = self.build({"NEW": make_bars(flat_then([101.0], length=60))})
        self.assertEqual(stats(market, "high120")["eligible"], 0)
        self.assertEqual(stats(market, "high20")["eligible"], 6)


def random_universe(seed, count=40, length=170, last=AS_OF):
    """隨機漫步股票：偶有缺日、零量、放量日與跳空；全部共用同一組平日，交易日曆即全部日期。"""
    rng = random.Random(seed)
    days = weekdays_ending(last, length)
    frames = {}
    for index in range(count):
        price = rng.uniform(20, 200)
        rows = []
        for i, day in enumerate(days):
            price *= 1 + rng.gauss(0.0008, 0.02)
            if rng.random() < 0.002:
                price *= rng.choice([0.5, 2.0])  # 疑似分割
            close = round(price, 2)
            high = round(close * (1 + abs(rng.gauss(0, 0.01))), 2)
            low = round(close * (1 - abs(rng.gauss(0, 0.01))), 2)
            volume = 0.0 if rng.random() < 0.02 else float(rng.randint(500, 1500) * (3 if rng.random() < 0.3 else 1))
            if rng.random() < 0.02 and i < length - 1:
                continue  # 缺日
            rows.append((day, high, low, close, volume))
        frame = pd.DataFrame(rows, columns=["Date", "High", "Low", "Close", "Volume"]).set_index("Date")
        frame.index = pd.DatetimeIndex(frame.index)
        frames[f"R{index:02d}"] = frame
    return frames


def naive_stats(frames, market_key, as_of):
    """逐檔逐日迴圈的參考實作（與向量化版本獨立）：回傳 {window: {strategy: (n, maturedN, wins, fails)}} 與基準。"""
    as_of = pd.Timestamp(as_of)
    calendar = sorted({day for frame in frames.values() for day in frame.index if day <= as_of})
    position = {day: i for i, day in enumerate(calendar)}
    stocks = {}
    for symbol, frame in frames.items():
        rows = [
            (day, max(high, close), min(low, close), close, volume)
            for day, high, low, close, volume in zip(
                frame.index, frame["High"].tolist(), frame["Low"].tolist(), frame["Close"].tolist(), frame["Volume"].tolist()
            )
            if day <= as_of and volume > 0 and close > 0
        ]
        if not rows or rows[-1][0] != as_of:
            continue
        cut = 0
        for i in range(1, len(rows)):
            if abs(rows[i][3] / rows[i - 1][3] - 1) > bo.JUMP_LIMIT:
                cut = i
        stocks[symbol] = rows[cut:]

    def prior_high(rows, i, n):
        return max(r[1] for r in rows[i - n:i]) if i >= n else None

    raw_cache = {}

    def raw(rows, i, n):
        key = (id(rows), n)
        if key not in raw_cache:
            levels = [prior_high(rows, j, n) for j in range(len(rows))]
            raw_cache[key] = [level is not None and rows[j][3] > level for j, level in enumerate(levels)]
        return raw_cache[key][i]

    def signal(rows, i, spec):
        n = spec["lookback"]
        if i < n + bo.COOLDOWN_DAYS or not raw(rows, i, n):
            return False
        if any(raw(rows, j, n) for j in range(i - bo.COOLDOWN_DAYS, i)):
            return False
        if spec["kind"] == "box":
            top = prior_high(rows, i, n)
            bottom = min(r[2] for r in rows[i - n:i])
            if (top - bottom) / bottom > bo.BOX_MAX_WIDTH[market_key] + 1e-12:
                return False
        if spec["volume"]:
            average = sum(r[4] for r in rows[i - bo.VOLUME_LOOKBACK:i]) / bo.VOLUME_LOOKBACK
            if rows[i][4] < bo.VOLUME_MULTIPLE * average:
                return False
        return True

    out = {}
    for window_key, days, _ in bo.WINDOWS:
        window_days = calendar[-days:]
        base = {}
        closes_by_day = [{r[0]: r[3] for r in rows} for rows in stocks.values()]
        last_closes = [rows[-1][3] for rows in stocks.values()]
        for day in window_days:
            returns = [last / closes[day] - 1 for closes, last in zip(closes_by_day, last_closes) if day in closes]
            if returns:
                base[day] = sum(1 for x in returns if x > 0) / len(returns)
        result = {}
        for spec in bo.STRATEGIES:
            n = matured = wins = fails = eligible = 0
            base_rates = []
            for rows in stocks.values():
                in_window = [i for i, r in enumerate(rows) if r[0] >= window_days[0]]
                if not in_window or in_window[0] < spec["lookback"] + bo.COOLDOWN_DAYS:
                    continue
                eligible += 1
                for i in in_window:
                    if signal(rows, i, spec):
                        n += 1
                        hold = position[as_of] - position[rows[i][0]]
                        if hold >= bo.MATURE_DAYS:
                            matured += 1
                            wins += rows[-1][3] > rows[i][3]
                            fails += rows[-1][3] < prior_high(rows, i, spec["lookback"])
                            base_rates.append(base[rows[i][0]])
                        break
            result[spec["key"]] = (eligible, n, matured, wins, fails, base_rates)
        out[window_key] = result
    return out


class NaiveReferenceTest(unittest.TestCase):
    def test_vectorised_matches_naive_loops_on_random_universes(self):
        checked = 0
        for seed in range(4):
            frames = random_universe(seed)
            for market_key in ("tw", "us"):
                market = bo.build_market(frames, "隨機", market_key)
                expected = naive_stats(frames, market_key, AS_OF)
                for window_key, per_strategy in expected.items():
                    for key, (eligible, n, matured, wins, fails, base_rates) in per_strategy.items():
                        got = stats(market, key, window_key)
                        label = f"seed={seed} {market_key} {window_key} {key}"
                        self.assertEqual(got["eligible"], eligible, label)
                        self.assertEqual(got["n"], n, label)
                        self.assertEqual(got["maturedN"], matured, label)
                        if matured:
                            self.assertEqual(got["winRate"], round(wins / matured * 100, 1), label)
                            self.assertEqual(got["failRate"], round(fails / matured * 100, 1), label)
                            self.assertEqual(got["baseRate"], round(float(np.mean(base_rates)) * 100, 1), label)
                            checked += 1
                        else:
                            self.assertIsNone(got["winRate"], label)
        self.assertGreater(checked, 20)  # 確認隨機資料真的產生了足夠的成熟突破


class NoLookAheadTest(unittest.TestCase):
    def test_truncating_data_after_as_of_does_not_change_results(self):
        frames = random_universe(7, length=190, last="2026-10-23")
        cutoff = pd.Timestamp(AS_OF)
        truncated = {symbol: frame[frame.index <= cutoff] for symbol, frame in frames.items()}
        full = bo.build_market(frames, "隨機", "tw", as_of=AS_OF)
        cut = bo.build_market(truncated, "隨機", "tw")
        self.assertEqual(cut["asOf"], AS_OF)
        self.assertEqual(full, cut)

    def test_mutating_future_bars_never_changes_past_signals(self):
        frames = random_universe(11, count=10, length=170)
        rng = random.Random(3)
        for frame in frames.values():
            clean, _ = bo.clean_bars(frame, frame.index[-1])
            split = len(clean) - 15
            base = bo.strategy_signals(clean, 0.14)
            future = clean.copy()
            factors = np.array([rng.uniform(0.5, 2.0) for _ in range(len(clean) - split)])[:, None]
            future.iloc[split:] = future.iloc[split:].to_numpy() * factors
            mutated = bo.strategy_signals(future, 0.14)
            for key in base:
                for left, right in zip(base[key], mutated[key]):
                    pd.testing.assert_series_equal(left.iloc[:split], right.iloc[:split], check_names=False)

    def test_future_data_after_as_of_does_not_leak_into_returns(self):
        # asOf 之後暴漲的資料不能影響報酬：指定 asOf 時結果與截斷一致（含勝率）
        closes = flat_then([101, 101, 101, 101, 101], length=60) + [300.0] * 5
        frames = {f"F{i}": make_bars([50.0] * 65, last="2026-10-16") for i in range(5)}
        frames["X"] = make_bars(closes, last="2026-10-16")
        market = bo.build_market(frames, "測試", "tw", as_of=AS_OF)
        result = stats(market, "high20")
        self.assertEqual((result["maturedN"], result["winRate"], result["avgReturn"]), (1, 0.0, 0.0))


class FrontendFixtureContractTest(unittest.TestCase):
    """atlas/tests/fixtures/breakouts/breakouts.sample.json（前端單元測試與 e2e 共用）必須與產出格式一致。"""

    def test_fixture_has_the_same_shape_as_the_job_output(self):
        path = os.path.join(ROOT, "atlas", "tests", "fixtures", "breakouts", "breakouts.sample.json")
        with open(path, encoding="utf-8") as file:
            fixture = json.load(file)
        self.assertEqual(fixture["params"], bo.params_meta())
        market = bo.build_market({**filler(), "X": make_bars(flat_then([101.0, 102, 102, 102]))}, "測試", "tw")
        for key in ("tw", "us"):
            sample = fixture["markets"][key]
            self.assertEqual(set(sample), set(market), key)
            self.assertEqual(set(sample["anomalies"]), set(market["anomalies"]), key)
            self.assertEqual(set(sample["windows"]), set(market["windows"]), key)
            for window_key, window in sample["windows"].items():
                produced = market["windows"][window_key]
                self.assertEqual(set(window), set(produced))
                self.assertEqual(set(window["market"]), set(produced["market"]))
                self.assertEqual(set(window["strategies"]), set(produced["strategies"]))
                for stats_ in window["strategies"].values():
                    self.assertEqual(set(stats_), set(produced["strategies"]["high20"]))


class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        self.out = os.path.join(self.tmp, "report", "breakouts.json")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, closes):
        days = weekdays_ending(AS_OF, len(closes))
        with open(os.path.join(self.data, name), "w", encoding="utf-8") as file:
            file.write("Date,Open,High,Low,Close,Volume\n")
            for day, close in zip(days, closes):
                file.write(f"{day.isoformat()},{close},{close},{close},{close},1000\n")

    def run_main(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = bo.main(["--data-dir", self.data, "--out", self.out], us_tickers_fn=None)
        return code, output.getvalue()

    def fill(self, tw=3, us=3):
        for i in range(tw):
            self.write(f"{1101 + i}.TW.csv", flat_then([101.0, 102, 102, 102]))
        for i in range(us):
            self.write(f"U{i:03d}.csv", flat_then([101.0, 100.5, 100.5, 100.5]))
        self.write("0050.TW.csv", [10.0] * 60)  # ETF：不在 universe
        self.write("^TWII.csv", [10.0] * 60)

    def test_writes_contract_fields(self):
        self.fill()
        with unittest.mock.patch.object(bo, "MIN_EVALUATED", 3):
            code, _ = self.run_main()
        self.assertEqual(code, 0)
        with open(self.out, encoding="utf-8") as file:
            payload = json.load(file)
        self.assertEqual(payload["version"], 1)
        datetime.fromisoformat(payload["generatedAt"])
        self.assertEqual([w["key"] for w in payload["params"]["windows"]], ["1w", "1m"])
        self.assertEqual(payload["params"]["matureDays"], 3)
        self.assertEqual(len(payload["params"]["strategies"]), 8)
        tw = payload["markets"]["tw"]
        self.assertEqual(tw["universeCount"], 3)
        self.assertEqual(tw["universe"], "台股上市櫃普通股（4碼、非0開頭）")
        result = tw["windows"]["1w"]["strategies"]["high20"]
        self.assertEqual(set(result), {
            "eligible", "n", "maturedN", "winRate", "avgReturn", "medianReturn",
            "failRate", "avgHoldDays", "baseRate", "baseAvgReturn",
        })
        self.assertEqual(result["winRate"], 100.0)
        self.assertEqual(payload["markets"]["us"]["windows"]["1w"]["strategies"]["high20"]["winRate"], 0.0)
        self.assertEqual(payload["markets"]["us"]["universe"], "S&P 500＋自選美股")

    def test_too_few_stocks_exits_1_and_keeps_the_existing_file(self):
        self.fill(tw=3, us=3)
        os.makedirs(os.path.dirname(self.out))
        with open(self.out, "w", encoding="utf-8") as file:
            file.write('{"old":true}')
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("不更新", output)
        self.assertIn(f"門檻 {bo.MIN_EVALUATED}", output)
        with open(self.out, encoding="utf-8") as file:
            self.assertEqual(file.read(), '{"old":true}')

    def test_missing_market_exits_1(self):
        self.fill(tw=3, us=0)
        with unittest.mock.patch.object(bo, "MIN_EVALUATED", 1):
            code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("US 沒有任何日 K", output)
        self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
