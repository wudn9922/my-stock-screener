"""四種成功口徑：共同分母、日線事件順序、開盤跳空與缺資料。"""
import sys
from pathlib import Path
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from breakout_outcomes import OUTCOME_KEYS, evaluate_attempt, first_hit_result, summarize_attempts


def future(rows):
    return pd.DataFrame(rows, columns=['Open', 'High', 'Low', 'Close'])


def attempt(rows=None, close=101, high=102, extra_days=5, missing=None):
    dates = pd.bdate_range('2026-09-01', periods=6)
    forward = rows or [[101, 103, 99, 102]] * 5
    frame = future([[100, high, 99, close]] + forward)
    frame.index = dates
    if missing is not None:
        frame = frame.drop(dates[missing])
    calendar = list(dates[:1 + extra_days])
    return evaluate_attempt(frame, 0, 100, pd.Series(5., index=frame.index), calendar, {d:i for i,d in enumerate(calendar)})


class FourOutcomesTest(unittest.TestCase):
    def test_common_failure_overrides_all_positive_followthrough_and_stays_denominator(self):
        result = attempt([[103,110,100,109]]*5, close=100.49)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['commonFailure'])
        self.assertFalse(any(result['wins'].values()))
        summary = summarize_attempts([result])
        self.assertEqual(summary['commonFailedN'], 1)
        self.assertTrue(all(r['evaluatedN']==1 and r['failures']==1 for r in summary['rules'].values()))

    def test_exact_close_gate_passes(self):
        self.assertFalse(attempt(close=100.5)['commonFailure'])

    def test_minus_five_touch_inclusive_fails_hold_but_not_the_other_rules_automatically(self):
        result = attempt([[101,106,95,102]] + [[101,103,99,102]]*4)
        self.assertFalse(result['wins']['hold5'])
        self.assertTrue(result['wins']['nextHigh'])
        self.assertTrue(result['wins']['targetAndNext'])
        self.assertFalse(result['wins']['firstHit'])  # same-bar unknown order

    def test_next_day_high_must_strictly_exceed_signal_high(self):
        result = attempt([[101,102,99,101]] + [[101,110,99,105]]*4)
        self.assertFalse(result['wins']['nextHigh'])
        self.assertFalse(result['wins']['targetAndNext'])
        self.assertTrue(result['wins']['hold5'])

    def test_stop_on_first_day_cannot_be_overridden_by_a_later_target(self):
        result = attempt([[100,101,94,96],[100,106,99,105]] + [[104,106,100,105]]*3)
        self.assertFalse(result['wins']['firstHit'])
        self.assertEqual(result['firstHitDay'], 1)
        self.assertEqual(result['firstHitReason'], 'stop')

    def test_target_before_later_stop_remains_success(self):
        result = attempt([[101,105,99,103],[100,101,94,96]] + [[98,101,97,99]]*3)
        self.assertTrue(result['wins']['firstHit'])
        self.assertFalse(result['wins']['hold5'])

    def test_next_day_higher_high_before_later_stop_remains_success(self):
        result = attempt([[101,103,99,102],[100,101,94,96]] + [[98,101,97,99]]*3)
        self.assertTrue(result['wins']['firstHit'])
        self.assertEqual(result['firstHitReason'], 'higher-high')
        self.assertFalse(result['wins']['targetAndNext'])

    def test_five_day_survival_without_target_or_next_high_succeeds_only_hold_and_first(self):
        result = attempt([[100,102,98,101]]*5)
        self.assertEqual(result['wins'], dict(hold5=True, nextHigh=False, firstHit=True, targetAndNext=False))
        self.assertEqual(result['firstHitReason'], 'held-five-days')

    def test_same_bar_positive_and_negative_wicks_are_conservative_failure(self):
        result = first_hit_result(future([[100,106,94,101]]),100,110)
        self.assertEqual(result, (False,1,'same-bar-unknown'))

    def test_same_bar_next_high_and_stop_are_also_unknown_order(self):
        self.assertEqual(first_hit_result(future([[100,103,94,101]]),100,102),(False,1,'same-bar-unknown'))

    def test_opening_gap_above_target_is_known_to_precede_the_low(self):
        self.assertEqual(first_hit_result(future([[106,108,94,101]]),100,110),(True,1,'success-at-open'))

    def test_opening_gap_below_stop_precedes_later_high(self):
        self.assertEqual(first_hit_result(future([[94,108,93,101]]),100,110),(False,1,'stop-at-open'))

    def test_opening_above_signal_high_is_a_known_first_event(self):
        self.assertEqual(first_hit_result(future([[103,104,94,101]]),100,102),(True,1,'success-at-open'))

    def test_new_high_alternative_is_available_only_on_the_next_day(self):
        result = attempt([[100,102,99,101],[100,103,94,96]] + [[98,101,97,99]]*3)
        self.assertFalse(result['wins']['firstHit'])
        self.assertEqual(result['firstHitDay'], 2)

    def test_target_and_next_high_does_not_add_a_stop_veto(self):
        result = attempt([[101,103,94,96],[100,101,94,96],[101,106,99,105]] + [[104,106,99,105]]*2)
        self.assertTrue(result['wins']['targetAndNext'])
        self.assertFalse(result['wins']['firstHit'])

    def test_pending_and_missing_are_not_losses_in_the_matched_cohort(self):
        pending = attempt(extra_days=4,close=99)
        missing = attempt(missing=2,close=99)
        complete = attempt()
        self.assertEqual(pending['status'],'pending')
        self.assertEqual(missing['status'],'missing')
        summary = summarize_attempts([pending,missing,complete])
        self.assertEqual((summary['n'],summary['completeN'],summary['pendingN'],summary['missingN']),(3,1,1,1))
        self.assertTrue(all(r['evaluatedN']==1 for r in summary['rules'].values()))

    def test_empty_cohort_has_no_fabricated_rate(self):
        summary = summarize_attempts([])
        self.assertEqual(set(summary['rules']),set(OUTCOME_KEYS))
        self.assertTrue(all(r['winRate'] is None for r in summary['rules'].values()))

    def test_later_bar_changes_do_not_rewrite_an_earlier_first_hit(self):
        early = [[101,103,99,102]]
        original = attempt(early+[[100,101,99,100]]*4)
        changed = attempt(early+[[100,110,90,100]]*4)
        self.assertEqual((original['wins']['firstHit'],original['firstHitDay']),
                         (changed['wins']['firstHit'],changed['firstHitDay']))


if __name__=='__main__':
    unittest.main()
