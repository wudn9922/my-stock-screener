"""
Pure-stdlib math for the US rates research page (scripts/build_rates.py).

Conventions used everywhere in this module:
- Dates are ISO strings (YYYY-MM-DD); series are ascending [(date, value)] lists or Series objects.
- Yields and yield changes are in percent / percentage points (FRED units). Spreads are in bp.
- Model duration / convexity use semiannual compounding with the yield as a decimal:
  dP/P ~= -D * dy + 1/2 * C * dy^2, D in years and C in years^2. iShares publishes convexity in a
  /100 unit (e.g. 3.6); the same number in this unit is 360.
"""
import bisect
import math
from datetime import date, timedelta

# Per-tenor look-back when a snapshot date has no value for that tenor ('.' or missing).
SNAPSHOT_LOOKBACK_DAYS = 5
# Inversion runs separated by at most this many non-inverted observations are one episode
# (the 10Y-2Y briefly touched zero in 2024 without ending the 2022-2024 inversion).
INVERSION_MERGE_GAP = 10

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
    """The two curve snapshots the page reads: today and one month back."""
    as_of = to_date(as_of)
    return [("now", as_of), ("1m", shift_months(as_of, -1))]


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
