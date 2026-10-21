"""Four fixed-horizon breakout outcomes, with causal first-event ordering.

All four use one cohort with five complete market sessions after each attempt.
Immediate close failures remain in that cohort's denominator; missing/future
candles never become losses. These labels do not imply an executable exit price.
"""
import math

import numpy as np

SUCCESS_MODEL = 'four-rules-v1'
GATE_ATR_PERIOD = 14
CLOSE_BUFFER_ATR = .1
OBSERVATION_DAYS = 5
BARRIER_PCT = .05
OUTCOME_SPECS = (
    {'key': 'hold5', 'label': '守住關鍵點', 'shortLabel': '五日守住',
     'definition': '突破後5個交易日內，最低價未碰到固定關鍵點下方5%即成功；碰到即失敗。'},
    {'key': 'nextHigh', 'label': '隔日再創高', 'shortLabel': '隔日創高',
     'definition': '下一個市場交易日最高價嚴格高於突破日最高價即成功，否則失敗。'},
    {'key': 'firstHit', 'label': '先觸發判定', 'shortLabel': '先觸發',
     'definition': '先碰關鍵點下方5%則失敗；先碰上方5%或隔日先創高則成功；5日都未碰下方5%也成功。先成立後不被後續走勢改寫。'},
    {'key': 'targetAndNext', 'label': '漲幅與續強', 'shortLabel': '目標＋續強',
     'definition': '突破後5個交易日內碰到固定關鍵點上方5%，且隔日最高價高於突破日最高價，兩項皆成立才成功；不另加下方5%否決。'},
)
OUTCOME_KEYS = tuple(s['key'] for s in OUTCOME_SPECS)


def qualifies_close(close, level, atr):
    return all(math.isfinite(float(x)) for x in (close, level, atr)) and atr > 0 and close >= level + CLOSE_BUFFER_ATR * atr


def first_hit_result(forward, level, signal_high):
    """An opening gap fixes order; otherwise simultaneous positive/negative wicks lose."""
    stop, target = level * (1 - BARRIER_PCT), level * (1 + BARRIER_PCT)
    for day, row in enumerate(forward.itertuples(index=False), 1):
        op, high, low = row.Open, row.High, row.Low
        # The next-day higher-high alternative is only available on D+1.
        if op <= stop:
            return False, day, 'stop-at-open'
        if op >= target or (day == 1 and op > signal_high):
            return True, day, 'success-at-open'
        negative = low <= stop
        positive = high >= target or (day == 1 and high > signal_high)
        if negative:
            return False, day, 'same-bar-unknown' if positive else 'stop'
        if positive:
            return True, day, 'higher-high' if day == 1 and high > signal_high else 'target'
    return True, OBSERVATION_DAYS, 'held-five-days'


def evaluate_attempt(bars, index, level, atr14, calendar, positions):
    day = bars.index[index]
    close = float(bars['Close'].iloc[index])
    atr = float(atr14.iloc[index])
    gate_valid = all(math.isfinite(x) for x in (close, level, atr)) and level > 0 and atr > 0
    gate_fail = not qualifies_close(close, level, atr) if gate_valid else None
    result = {'status': 'missing', 'commonFailure': gate_fail, 'wins': {}, 'firstHitReason': None}
    if not gate_valid:
        return result
    p = positions[day]
    if p + OBSERVATION_DAYS >= len(calendar):
        return {**result, 'status': 'pending'}
    expected = calendar[p + 1:p + OBSERVATION_DAYS + 1]
    locs = bars.index.get_indexer(expected)
    if (locs < 0).any():
        return result
    forward = bars.iloc[locs][['Open', 'High', 'Low', 'Close']]
    if not np.isfinite(forward.to_numpy()).all() or (forward.to_numpy() <= 0).any():
        return result
    result['status'] = 'complete'
    if gate_fail:
        result['wins'] = {key: False for key in OUTCOME_KEYS}
        result['firstHitReason'] = 'signal-close-failed'
        return result
    stop = bool((forward['Low'] <= level * (1 - BARRIER_PCT)).any())
    target = bool((forward['High'] >= level * (1 + BARRIER_PCT)).any())
    next_high = float(forward['High'].iloc[0]) > float(bars['High'].iloc[index])
    first_win, first_day, reason = first_hit_result(forward, level, float(bars['High'].iloc[index]))
    result.update(firstHitDay=first_day, firstHitReason=reason, wins={
        'hold5': not stop,
        'nextHigh': next_high,
        'firstHit': first_win,
        'targetAndNext': target and next_high,
    })
    return result


def summarize_attempts(attempts):
    complete = [a for a in attempts if a['status'] == 'complete']
    n = len(complete)
    return {
        'model': SUCCESS_MODEL,
        'n': len(attempts),
        'completeN': n,
        'pendingN': sum(a['status'] == 'pending' for a in attempts),
        'missingN': sum(a['status'] == 'missing' for a in attempts),
        'commonFailedN': sum(a['commonFailure'] is True for a in complete),
        'sameBarFailedN': sum(a['firstHitReason'] == 'same-bar-unknown' for a in complete),
        'rules': {key: {
            'wins': sum(bool(a['wins'][key]) for a in complete),
            'failures': sum(not a['wins'][key] for a in complete),
            'evaluatedN': n,
            'winRate': round(sum(bool(a['wins'][key]) for a in complete) / n * 100, 1) if n else None,
        } for key in OUTCOME_KEYS},
    }


def success_params():
    return {
        'successModel': SUCCESS_MODEL, 'successAtrPeriod': GATE_ATR_PERIOD,
        'closeBufferAtr': CLOSE_BUFFER_ATR, 'observationDays': OBSERVATION_DAYS,
        'barrierPct': BARRIER_PCT * 100, 'sameBarPolicy': 'conservative-failure',
        'outcomeRules': list(OUTCOME_SPECS),
    }
