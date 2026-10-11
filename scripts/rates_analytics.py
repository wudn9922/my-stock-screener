"""
Pure-stdlib math for the US rates research page (scripts/build_rates.py).

Conventions used everywhere in this module:
- Dates are ISO strings (YYYY-MM-DD); series are ascending [(date, value)] lists or Series objects.
- Yields and yield changes are in percent / percentage points (FRED units). Spreads are in bp.
- Returns, volatility and drawdowns are returned in percent and NOT rounded (the builder rounds).
- Empirical sensitivity: beta = OLS slope of the ETF's simple return (decimal) on the yield change
  (percentage points) between consecutive dates where BOTH have a value; durEmp = -100 * beta (years).
  Intersecting the dates first handles bond-market holidays (stocks open, no H.15 value) and stock
  holidays (H.15 value, no ETF close): the return and the yield change always span the same interval.
- Model duration / convexity use semiannual compounding with the yield as a decimal:
  dP/P ~= -D * dy + 1/2 * C * dy^2, D in years and C in years^2. iShares publishes convexity in a
  /100 unit (e.g. 3.6); the same number in this unit is 360.
"""
import bisect
import math
from datetime import date, timedelta

# A window whose first observation is this many days after the window start is not reported.
WINDOW_GRACE_DAYS = 7
# Per-tenor look-back when a snapshot date has no value for that tenor ('.' or missing).
SNAPSHOT_LOOKBACK_DAYS = 5
# Inversion runs separated by at most this many non-inverted observations are one episode
# (the 10Y-2Y briefly touched zero in 2024 without ending the 2022-2024 inversion).
INVERSION_MERGE_GAP = 10
TRADING_DAYS = 252
MIN_VOL_RETURNS = 20
MIN_REGRESSION_POINTS = {"daily": 100, "weekly": 52}

# Equal-par ladders with a bond every half year from `low` to `high` years, each priced at the
# yield linearly interpolated between DGS20 and DGS30 (flat outside 20-30y).
MODEL_PROXIES = {
    "par-20-30": {"low": 20, "high": 30, "coupon": True, "label": "equal-par par-coupon ladder 20–30y"},
    "zero-20-30": {"low": 20, "high": 30, "coupon": False, "label": "equal-par zero ladder 20–30y"},
    "zero-25-30": {"low": 25, "high": 30, "coupon": False, "label": "equal-par zero ladder 25–30y"},
}


class Series:
    """Ascending (date, value) observations with on-or-before lookups. Drops None / non-finite values."""

    def __init__(self, points):
        clean = {}
        for day, value in points:
            if value is None:
                continue
            value = float(value)
            if math.isfinite(value):
                clean[day] = value
        self.dates = sorted(clean)
        self.values = [clean[day] for day in self.dates]

    def __len__(self):
        return len(self.dates)

    def points(self):
        return list(zip(self.dates, self.values))

    def last(self):
        return (self.dates[-1], self.values[-1]) if self.dates else None

    def index_on_or_before(self, day):
        """Index of the last observation on or before `day` (ISO string or date), -1 if none."""
        return bisect.bisect_right(self.dates, iso(day)) - 1

    def on_or_before(self, day, max_days=None):
        index = self.index_on_or_before(day)
        if index < 0:
            return None
        if max_days is not None and (to_date(day) - to_date(self.dates[index])).days > max_days:
            return None
        return self.dates[index], self.values[index]

    def value_at(self, day):
        index = self.index_on_or_before(day)
        if index >= 0 and self.dates[index] == iso(day):
            return self.values[index]
        return None


def as_series(value):
    return value if isinstance(value, Series) else Series(value)


def iso(day):
    return day if isinstance(day, str) else day.isoformat()


def to_date(day):
    return date.fromisoformat(day) if isinstance(day, str) else day


def shift_months(day, months):
    """Same day-of-month `months` later (negative = earlier), clamped to the month end."""
    day = to_date(day)
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    if month == 12:
        last = 31
    else:
        last = (date(year, month + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def shift_years(day, years):
    return shift_months(day, 12 * years)


# --------------------------------------------------------------------------- sampling and snapshots


def valid_iso(day):
    """True for a real YYYY-MM-DD date string."""
    try:
        return len(day) == 10 and date.fromisoformat(day).isoformat() == day
    except (TypeError, ValueError):
        return False


def iso_week(day):
    """(ISO year, ISO week) of a date."""
    return tuple(to_date(day).isocalendar()[:2])


def weekly_sample(dates):
    """Last date of every ISO week (a holiday Friday falls back to Thursday; the current partial week is kept)."""
    out = []
    previous_week = None
    for day in sorted(dates):
        week = iso_week(day)
        if week == previous_week:
            out[-1] = day
        else:
            out.append(day)
        previous_week = week
    return out


def snapshot_targets(as_of):
    as_of = to_date(as_of)
    return [
        ("now", as_of),
        ("1w", as_of - timedelta(days=7)),
        ("1m", shift_months(as_of, -1)),
        ("3m", shift_months(as_of, -3)),
        ("1y", shift_years(as_of, -1)),
        ("2y", shift_years(as_of, -2)),
    ]


def snapshot_curve(series_map, ids, anchor, target, lookback=SNAPSHOT_LOOKBACK_DAYS):
    """Curve on the last anchor (DGS10) date on or before `target`; each tenor may look back `lookback` days.

    Returns {"date", "values"} (values rounded to 3 decimals, None when missing) or None.
    """
    point = as_series(anchor).on_or_before(target)
    if point is None:
        return None
    day = point[0]
    values = []
    for key in ids:
        series = series_map.get(key)
        found = series.on_or_before(day, lookback) if series is not None else None
        values.append(round(found[1], 3) + 0.0 if found else None)
    return {"date": day, "values": values}


# --------------------------------------------------------------------------- spreads


def spread_points(long, short):
    """(date, long - short in bp) on dates where both series have a value."""
    long, short = as_series(long), as_series(short)
    lookup = dict(zip(short.dates, short.values))
    return [(day, (value - lookup[day]) * 100) for day, value in zip(long.dates, long.values) if day in lookup]


def _runs(points, merge_gap):
    runs = []
    current = None
    gap = 0
    for index, (day, value) in enumerate(points):
        if value < 0:
            if current is not None and gap <= merge_gap:
                current["endIndex"] = index
            else:
                if current is not None:
                    runs.append(current)
                current = {"startIndex": index, "endIndex": index, "min": value, "minIndex": index}
            if value < current["min"]:
                current["min"], current["minIndex"] = value, index
            gap = 0
        else:
            gap += 1
    if current is not None:
        runs.append(current)
    return runs


def _public_run(run, points, open_ended=False):
    return {
        "start": points[run["startIndex"]][0],
        "end": None if open_ended else points[run["endIndex"]][0],
        "minBp": int(round(run["min"])),
        "minDate": points[run["minIndex"]][0],
    }


def inversion_runs(points, merge_gap=INVERSION_MERGE_GAP):
    """Episodes of spread < 0: [{start, end, minBp, minDate}]."""
    return [_public_run(run, points) for run in _runs(points, merge_gap)]


def percentile_rank(values, current):
    """Share of values below `current` (ties count half), 0-100."""
    if not values:
        return None
    below = sum(1 for value in values if value < current)
    equal = sum(1 for value in values if value == current)
    return (below + 0.5 * equal) / len(values) * 100


def spread_stats(points, merge_gap=INVERSION_MERGE_GAP):
    """Current value, 1-month change, inversion state and 5-year percentile of a bp spread series.

    invertedDays counts observations (trading days) from the start of the current episode.
    """
    if not points:
        return None
    last_day, last_value = points[-1]
    series = Series(points)
    prior = series.on_or_before(shift_months(last_day, -1))
    runs = _runs(points, merge_gap)
    inverted = last_value < 0
    last_run = runs[-1] if runs else None
    inverted_days = len(points) - last_run["startIndex"] if inverted and last_run else 0
    floor = shift_years(last_day, -5).isoformat()
    recent = [value for day, value in points if day >= floor]
    return {
        "valueBp": int(round(last_value)),
        "chg1mBp": int(round(last_value - prior[1])) if prior else None,
        "inverted": inverted,
        "invertedDays": inverted_days,
        "lastInversion": _public_run(last_run, points, open_ended=inverted) if last_run else None,
        "pct5y": int(round(percentile_rank(recent, last_value))),
    }


# --------------------------------------------------------------------------- returns and risk


def window_base(series, start):
    """Index of the observation a window starting at `start` is measured from, or None.

    The last observation on or before `start`; if the data begins after `start`, its first observation
    when that is within WINDOW_GRACE_DAYS.
    """
    if not len(series):
        return None
    index = series.index_on_or_before(start)
    if index >= 0:
        return index
    if (to_date(series.dates[0]) - to_date(start)).days <= WINDOW_GRACE_DAYS:
        return 0
    return None


def total_return(series, start):
    """(base date, total return %, days) from the window base to the last observation, or None."""
    series = as_series(series)
    index = window_base(series, start)
    if index is None or index >= len(series) - 1 or series.values[index] <= 0:
        return None
    days = (to_date(series.dates[-1]) - to_date(series.dates[index])).days
    return series.dates[index], (series.values[-1] / series.values[index] - 1) * 100, days


def annualize(total_pct, days):
    """CAGR in % = (P1/P0)^(365.25/days) - 1."""
    if days <= 0 or total_pct <= -100:
        return None
    return ((1 + total_pct / 100) ** (365.25 / days) - 1) * 100


def period_returns(series):
    """{"1y": {total}, "3y"/"5y"/"10y": {total, annualized} | None, "inception": {start, total, annualized}}."""
    series = as_series(series)
    if len(series) < 2:
        return None
    end = to_date(series.dates[-1])
    out = {}
    for key, years in (("1y", 1), ("3y", 3), ("5y", 5), ("10y", 10)):
        result = total_return(series, shift_years(end, -years))
        if result is None:
            out[key] = None
        elif years == 1:
            out[key] = {"total": result[1]}
        else:
            out[key] = {"total": result[1], "annualized": annualize(result[1], result[2])}
    start = series.dates[0]
    days = (end - to_date(start)).days
    total = (series.values[-1] / series.values[0] - 1) * 100
    out["inception"] = {"start": start, "total": total, "annualized": annualize(total, days) if days >= 365 else None}
    return out


def ann_vol(series, start):
    """Annualized volatility in %: sample stdev of daily log returns since the window base x sqrt(252)."""
    series = as_series(series)
    index = window_base(series, start)
    if index is None:
        return None
    values = series.values[index:]
    logs = [math.log(b / a) for a, b in zip(values, values[1:]) if a > 0 and b > 0]
    if len(logs) < MIN_VOL_RETURNS:
        return None
    mean = sum(logs) / len(logs)
    variance = sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS) * 100


def max_drawdown(series, start=None):
    """Deepest peak-to-trough fall in %: {value, peak, trough, recovered (first date back at the peak) | None}."""
    series = as_series(series)
    first = 0
    if start is not None:
        first = window_base(series, start)
        if first is None:
            return None
    dates, values = series.dates[first:], series.values[first:]
    if not values:
        return None
    worst, worst_peak, worst_trough = 0.0, None, None
    peak_index = 0
    for index, value in enumerate(values):
        if value > values[peak_index]:
            peak_index = index
        drawdown = (value / values[peak_index] - 1) * 100
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_index, index
    if worst_peak is None:
        return {"value": 0.0, "peak": None, "trough": None, "recovered": None}
    recovered = None
    for index in range(worst_trough + 1, len(values)):
        if values[index] >= values[worst_peak]:
            recovered = dates[index]
            break
    return {"value": worst, "peak": dates[worst_peak], "trough": dates[worst_trough], "recovered": recovered}


def current_drawdown(series):
    series = as_series(series)
    if not len(series):
        return None
    return (series.values[-1] / max(series.values) - 1) * 100


def filter_bad_points(points, threshold, confirm=2):
    """Drops isolated price spikes: a one-day move beyond `threshold` (fraction) that the next `confirm`
    observations do not confirm. A confirmed level change is kept. Returns (kept, dropped dates)."""
    kept, dropped = [], []
    for index, (day, value) in enumerate(points):
        if value is None or not value > 0:
            dropped.append(day)
            continue
        if not kept or abs(value / kept[-1][1] - 1) <= threshold:
            kept.append((day, value))
            continue
        following = [v for _, v in points[index + 1 : index + 1 + confirm] if v is not None and v > 0]
        level = value
        confirmed = len(following) == confirm
        for nxt in following:
            if abs(nxt / level - 1) > threshold:
                confirmed = False
                break
            level = nxt
        if confirmed:
            kept.append((day, value))
        else:
            dropped.append(day)
    return kept, dropped


# --------------------------------------------------------------------------- empirical duration


def ols(x, y):
    """(slope, intercept, r2) of y on x, or None when x has no variance."""
    n = len(x)
    if n != len(y) or n < 3:
        return None
    mean_x, mean_y = sum(x) / n, sum(y) / n
    sxx = sum((a - mean_x) ** 2 for a in x)
    syy = sum((b - mean_y) ** 2 for b in y)
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    if sxx <= 0:
        return None
    slope = sxy / sxx
    r2 = sxy * sxy / (sxx * syy) if syy > 0 else 0.0
    return slope, mean_y - slope * mean_x, r2


def aligned_changes(etf, yields, start, freq):
    """(dy in pp, ETF simple returns) between consecutive dates on which both have a value.

    freq "weekly" first keeps the last common date of every ISO week (Friday to Friday, or the last
    trading day before it). Only intervals starting on or after `start` are used.
    """
    etf, yields = as_series(etf), as_series(yields)
    prices = dict(zip(etf.dates, etf.values))
    levels = dict(zip(yields.dates, yields.values))
    common = [day for day in yields.dates if day in prices and prices[day] > 0]
    if freq == "weekly":
        common = weekly_sample(common)
    floor = iso(start)
    dy, returns = [], []
    for a, b in zip(common, common[1:]):
        if a < floor:
            continue
        dy.append(levels[b] - levels[a])
        returns.append(prices[b] / prices[a] - 1)
    return dy, returns


def regress_beta(etf, yields, start, freq):
    """{"beta", "durEmp", "r2", "n"} of ETF returns on yield changes since `start`, or None if too short."""
    dy, returns = aligned_changes(etf, yields, start, freq)
    if len(dy) < MIN_REGRESSION_POINTS[freq]:
        return None
    fit = ols(dy, returns)
    if fit is None:
        return None
    beta, _, r2 = fit
    return {"beta": round(beta, 5), "durEmp": round(-100 * beta, 2), "r2": round(r2, 3), "n": len(dy)}


# --------------------------------------------------------------------------- model duration / convexity


def bond_measures(cashflows, y):
    """(price, modified duration, convexity) of [(t years, amount)] at yield y (decimal, semiannual)."""
    base = 1 + y / 2
    price = duration = convexity = 0.0
    for t, amount in cashflows:
        pv = amount * base ** (-2 * t)
        price += pv
        duration += t * pv
        convexity += t * (t + 0.5) * pv
    return price, duration / price / base, convexity / price / base ** 2


def par_cashflows(maturity, y, face=100.0):
    """Semiannual coupon bond priced at par at yield y (decimal)."""
    periods = int(round(maturity * 2))
    flows = [(k / 2, face * y / 2) for k in range(1, periods + 1)]
    flows[-1] = (flows[-1][0], flows[-1][1] + face)
    return flows


def interp_yield(t, y20, y30):
    """Yield in % at maturity t, linear between 20y and 30y and flat outside."""
    t = min(max(t, 20.0), 30.0)
    return y20 + (y30 - y20) * (t - 20.0) / 10.0


def _ladder(low, high, y20, y30, coupon):
    steps = int(round((high - low) * 2))
    for k in range(steps + 1):
        t = low + k / 2
        y = interp_yield(t, y20, y30) / 100
        yield t, y, (par_cashflows(t, y) if coupon else [(t, 100.0)])


def ladder_value(low, high, y20, y30, coupon, shift=0.0):
    """Value of the ladder (cash flows fixed at today's yields) after a parallel shift in pp."""
    return sum(bond_measures(flows, y + shift / 100)[0] for _, y, flows in _ladder(low, high, y20, y30, coupon))


def ladder_measures(low, high, y20, y30, coupon):
    """(duration, convexity) of the ladder for a parallel shift: price-weighted averages over its bonds."""
    total = duration = convexity = 0.0
    for _, y, flows in _ladder(low, high, y20, y30, coupon):
        price, d, c = bond_measures(flows, y)
        total += price
        duration += price * d
        convexity += price * c
    return duration / total, convexity / total


def model_duration_convexity(proxy, dgs20, dgs30):
    """{"duration": years, "convexity": years², "proxy": label} from today's DGS20/DGS30, or None."""
    spec = MODEL_PROXIES.get(proxy)
    if spec is None or dgs20 is None or dgs30 is None:
        return None
    duration, convexity = ladder_measures(spec["low"], spec["high"], dgs20, dgs30, spec["coupon"])
    return {"duration": round(duration, 2), "convexity": round(convexity, 1), "proxy": spec["label"]}
