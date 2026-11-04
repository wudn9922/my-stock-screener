"""python -m pytest tests -q"""
import json

import numpy as np
import pandas as pd
import pytest

from scripts.breadth_signals import core, daily, rules as R


def bdays(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


def bars_from(close, idx, spread=0.002, opens=None):
    c = pd.Series(close, index=idx, dtype=float)
    o = c.shift(1).fillna(c) if opens is None else pd.Series(opens, index=idx, dtype=float)
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + spread), "Low": np.minimum(o, c) * (1 - spread), "Close": c})


# ---------------------------------------------------------------- core（與網站 build_breadth 同定義）
def test_breadth_strictly_above_and_zero_volume_not_counted():
    idx = bdays(25)
    close = pd.DataFrame({"A": np.arange(25, dtype=float) + 10,          # 一路漲 → 站上
                          "B": np.r_[np.full(24, 10.0), 10.0],           # 等於均線 → 不算站上（嚴格大於）
                          "C": np.arange(25, 0, -1, dtype=float)}, idx)  # 一路跌
    vol = pd.DataFrame(1000.0, index=idx, columns=close.columns)
    vol.iloc[-1, 2] = 0                                                  # C 最後一天零成交 → 不在分母
    h = core.breadth_history(close, vol, periods=(20,))
    last = h.iloc[-1]
    assert last["total20"] == 2 and last["above20"] == 1 and last["pct20"] == 50.0


def test_sma_uses_each_stock_own_bars_and_skips_weekend_rows():
    idx = pd.date_range("2024-01-01", periods=40, freq="D")           # 含週末
    close = pd.DataFrame({"A": np.linspace(10, 20, 40)}, idx)
    vol = pd.DataFrame(1.0, index=idx, columns=["A"])
    h = core.breadth_history(close, vol, periods=(20,))
    assert (h.index.dayofweek < 5).all()
    assert h["pct20"].dropna().eq(100.0).all()


# ---------------------------------------------------------------- rules
TH = {"p5": 10.0, "p10": 20.0, "p50": 50.0, "p90": 80.0, "p95": 85.0}


@pytest.mark.parametrize("pct,zone", [(9, "extreme_oversold"), (10, "extreme_oversold"), (20, "oversold"),
                                      (20.1, "neutral"), (79.9, "neutral"), (80, "overbought"),
                                      (85, "extreme_overbought"), (None, "none")])
def test_zone_boundaries(pct, zone):
    assert R.zone(pct, TH) == zone


def test_low_signal_needs_both_conditions_and_cooldown():
    idx = bdays(120)
    close = pd.Series(np.r_[np.full(70, 100.0), np.linspace(100, 90, 50)], idx)   # 從第 70 天開始跌
    pct = pd.Series(50.0, idx)
    pct.iloc[80:83] = 15        # 寬度超賣但指數只跌約 2% → 不觸發
    pct.iloc[100:103] = 15      # 指數已跌 > 5% → 觸發
    pct.iloc[105:107] = 15      # 5 天後再進超賣區 → cooldown 內不算
    f = R.low_signal_frame(pct, close, p10=20, swing=0.05)
    sig = f.index[f["signal"]]
    assert list(sig) == [idx[100]]
    assert f["a"].iloc[81] and not f["b"].iloc[81]


def test_trade_target_stop_and_same_day_rule():
    idx = bdays(10)
    b = bars_from([100] * 10, idx)
    b.loc[idx[3], "High"] = 106                     # 第 3 天碰停利
    t = R.simulate_trade(b, idx[0], 0.05)
    assert t.outcome == "target" and t.exit == pytest.approx(105) and t.entry_date == idx[1]
    b.loc[idx[3], "Low"] = 94                       # 同一天也碰停損 → 保守算停損
    assert R.simulate_trade(b, idx[0], 0.05).outcome == "stop"


def test_gap_through_stop_fills_at_open():
    idx = bdays(10)
    b = bars_from([100] * 10, idx)
    b.loc[idx[4], ["Open", "Low"]] = [90, 89]
    t = R.simulate_trade(b, idx[0], 0.05)
    assert t.outcome == "stop" and t.exit == pytest.approx(90)


def test_open_and_pending_trades():
    idx = bdays(30)
    b = bars_from([100] * 30, idx)
    t = R.simulate_trade(b, idx[10], 0.05)
    assert t.outcome == "open" and t.days == 19 and t.expires > idx[-1]
    assert R.simulate_trade(b, idx[-1], 0.05).outcome == "pending"


# ---------------------------------------------------------------- daily
def site_json(dates, m20, m60):
    return {"markets": {"us": {"asOf": dates[-1], "segments": {"all": {"count": 5000, "ma20": {"pct": m20[-1], "total": 4900},
                                                                      "ma60": {"pct": m60[-1], "total": 4800}}},
                               "history": {"dates": dates, "ma20Pct": m20, "ma60Pct": m60}}}}


def test_merge_prefers_site_and_reports_overlap():
    idx = bdays(5)
    hist = pd.DataFrame({"pct20": [50, 51, 52, 53, 54.0], "pct60": [60.0] * 5}, idx)
    hist.index.name = "date"
    new_days = [str(d.date()) for d in idx[3:]] + [str((idx[-1] + pd.offsets.BDay(1)).date())]
    site = daily.site_us_breadth(site_json(new_days, [55, 56, 57], [61, 62, 63]))
    merged, check = daily.merge_history(hist, site)
    assert len(merged) == 6 and merged["pct20"].iloc[-1] == 57 and merged["pct20"].iloc[3] == 55
    assert check["overlapDays"] == 2 and check["maxAbsDiffMa20"] == 2.0


@pytest.mark.parametrize("failure,expected_download_calls,reason", [
    ("overlap", 0, "長歷史與網站寬度差"),
    ("download", 1, "index download failed"),
    ("missing-bars", 1, "指數日 K 缺少"),
    ("empty-bars", 1, "下載不完整"),
    ("short-bars", 1, "下載不完整"),
    ("stale-bars", 1, "與寬度日期"),
])
def test_daily_failures_leave_history_and_signals_untouched(tmp_path, capsys, failure, expected_download_calls, reason):
    idx = bdays(5)
    hist = pd.DataFrame({"pct20": [50.0] * 5, "pct60": [60.0] * 5}, index=idx)
    hist.index.name = "date"
    history_path = tmp_path / "history.csv"
    hist.to_csv(history_path)
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration()), encoding="utf-8")
    breadth_path = tmp_path / "breadth.json"
    values20 = [80.0] * 5 if failure == "overlap" else [50.0] * 5
    values60 = [60.0] * 5
    breadth_path.write_text(json.dumps(site_json([str(d.date()) for d in idx], values20, values60)), encoding="utf-8")
    out_path = tmp_path / "breadth-signals.json"
    out_path.write_text('{"previous":"signal"}', encoding="utf-8")
    before_history, before_signals = history_path.read_bytes(), out_path.read_bytes()
    download_calls = []

    def load_failed_or_partial_bars(_tickers):
        download_calls.append(True)
        if failure == "download":
            raise RuntimeError("index download failed")
        last_day = idx[-2] if failure == "stale-bars" else idx[-1]
        bar_days = pd.bdate_range(end=last_day, periods=400)
        result = {ticker: bars_from(np.full(400, 100.0), bar_days) for ticker in R.INDICES}
        if failure == "missing-bars":
            result.pop("^SOX")
        elif failure == "empty-bars":
            result["^SOX"] = pd.DataFrame(columns=["Open", "High", "Low", "Close"])
        elif failure == "short-bars":
            result["^SOX"] = bars_from(np.full(20, 100.0), bar_days[-20:])
        return result

    result = daily.main([
        "--calibration", str(calibration_path),
        "--history", str(history_path),
        "--breadth", str(breadth_path),
        "--out", str(out_path),
    ], bars_loader=load_failed_or_partial_bars)

    assert result == 1
    assert reason in capsys.readouterr().out
    assert len(download_calls) == expected_download_calls
    assert history_path.read_bytes() == before_history
    assert out_path.read_bytes() == before_signals


def calibration():
    th = {"ma20": dict(TH), "ma60": dict(TH)}
    stats = {"trades": 70, "hitRate": 0.66, "hitRateEarly": 0.64, "hitRateLate": 0.68, "baseRate": 0.48,
             "avgReturn": 0.012, "medianDays": 18, "winnersMaeP90": -0.04, "nearSwingLowRate": 0.5, "events": []}
    return {"generatedAt": "2026-11-01T00:00:00+00:00", "rulesVersion": 1, "sample": {}, "thresholds": th,
            "indices": {t: {"name": m["name"], "swing": m["swing"], **stats} for t, m in R.INDICES.items()},
            "overbought": {"dropFirstRate": 0.33, "baseRate": 0.33, "events": 40}}


def test_daily_accepts_complete_aligned_indices_and_appends_the_site_session(tmp_path):
    idx = bdays(6)
    hist = pd.DataFrame({"pct20": [50.0] * 5, "pct60": [60.0] * 5}, index=idx[:5])
    hist.index.name = "date"
    history_path = tmp_path / "history.csv"
    hist.to_csv(history_path)
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration()), encoding="utf-8")
    breadth_path = tmp_path / "breadth.json"
    breadth_path.write_text(json.dumps(site_json([str(d.date()) for d in idx], [50.0] * 5 + [51.0], [60.0] * 6)), encoding="utf-8")
    source_bytes = breadth_path.read_bytes()
    out_path = tmp_path / "breadth-signals.json"
    bar_days = pd.bdate_range(end=idx[-1], periods=400)
    bars = {ticker: bars_from(np.full(400, 100.0), bar_days) for ticker in R.INDICES}
    result = daily.main([
        "--calibration", str(calibration_path), "--history", str(history_path),
        "--breadth", str(breadth_path), "--out", str(out_path),
    ], bars_loader=lambda _tickers: bars)
    assert result == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))["markets"]["us"]
    assert payload["asOf"] == str(idx[-1].date())
    assert payload["readings"]["ma20"]["pct"] == 51.0
    assert {index["symbol"] for index in payload["lowSignal"]["indices"]} == set(R.INDICES)
    assert len(daily.load_history(history_path)) == 6
    assert breadth_path.read_bytes() == source_bytes


def scenario(last_pct, drop):
    idx = bdays(300)
    pct = pd.Series(50.0, idx)
    pct.iloc[-1] = last_pct
    hist = pd.DataFrame({"pct20": pct, "pct60": pct}, idx)
    close = np.r_[np.full(299, 100.0), 100 * (1 - drop)]
    bars = {t: bars_from(close, idx) for t in R.INDICES}
    return hist, bars


def test_build_new_signal_watch_and_tw_not_applicable():
    hist, bars = scenario(15, 0.08)        # 寬度超賣＋各指數跌 8%：S&P/道瓊/那斯達克/羅素觸發，費半（10%）只 watch
    out = daily.build(calibration(), hist, bars, {"overlapDays": 0}, "2026-11-01T00:00:00+00:00")
    states = {x["symbol"]: x["state"] for x in out["markets"]["us"]["lowSignal"]["indices"]}
    assert states["^GSPC"] == "new" and states["^SOX"] == "watch"
    sp = next(x for x in out["markets"]["us"]["lowSignal"]["indices"] if x["symbol"] == "^GSPC")
    assert sp["trade"]["entryRef"] == pytest.approx(92) and sp["trade"]["stop"] == pytest.approx(92 * 0.95)
    assert out["markets"]["us"]["readings"]["ma20"]["zone"] == "oversold"
    assert out["markets"]["tw"]["applicable"] is False
    json.dumps(out, allow_nan=False)        # 網站寫檔用 allow_nan=False


def test_build_quiet_market_has_no_signal():
    hist, bars = scenario(50, 0.0)
    out = daily.build(calibration(), hist, bars, {"overlapDays": 0}, "x")
    assert all(x["state"] == "none" for x in out["markets"]["us"]["lowSignal"]["indices"])
    assert out["markets"]["us"]["overbought"]["active"] is False
