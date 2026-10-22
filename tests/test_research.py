"""Research-v1 semantic tests. Synthetic rows are confined to tests, never production reports."""

import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_io import SourceError, retain, read, write
from research_metrics import (
    volume_features,
    volume_stats,
    structure,
    cost_estimate,
    institutional_summary,
)
from research_sources import parse_flows, parse_alerts
from build_tw_chips import parse_tdcc_distribution
from build_sec_radar import (
    event,
    submissions,
    parse_index,
    extract,
    SecHttp,
    Cooldown,
    NotPublished,
    radar_events,
)
from build_world_assets import changes, parse_ecb, parse_h10, parse_eia, parse_wb

FIX = Path(__file__).parent / "fixtures/research"


def bars(n=45):
    from datetime import timedelta

    return [
        dict(
            date=(date(2026, 1, 1) + timedelta(days=i)).isoformat(),
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=100,
            amount=None,
            action=None,
        )
        for i in range(n)
    ]


def flow(day, net):
    return dict(
        date=day,
        foreign=dict(buy=max(net, 0), sell=max(-net, 0), net=net),
        trust=dict(net=net),
        dealer=dict(net=net),
    )


class OfficialFormats(unittest.TestCase):
    def test_twse_shares_negative(self):
        r = parse_flows((FIX / "twset86.json").read_bytes(), "TW", "2026-10-20")
        self.assertEqual(r["3231.TW"]["foreign"]["net"], 32813179)
        self.assertEqual(r["2002.TW"]["dealer"]["net"], -39290)

    def test_twse_reordered_headers(self):
        d = json.loads((FIX / "twset86.json").read_text())
        order = list(range(len(d["fields"])))[::-1]
        d["fields"] = [d["fields"][i] for i in order]
        d["data"] = [[row[i] for i in order] for row in d["data"]]
        self.assertEqual(
            parse_flows(d, "TW", "2026-10-20")["3231.TW"]["foreign"]["buy"], 48976463
        )

    def test_tpex_json_and_big5_csv_agree(self):
        j = parse_flows((FIX / "tpext86.json").read_bytes(), "TWO", "2026-10-20")
        c = parse_flows((FIX / "tpex.csv").read_bytes(), "TWO", "2026-10-20")
        self.assertGreater(len(j), 0)
        self.assertEqual(j, c)

    def test_tpex_full_schema_unknown_rejected(self):
        d = json.loads((FIX / "tpext86.json").read_text())
        d["tables"][0]["fields"][0] = "unknown"
        with self.assertRaises(SourceError):
            parse_flows(d, "TWO", "2026-10-20")

    def test_tpex_csv_official_date_must_match(self):
        with self.assertRaises(SourceError):
            parse_flows((FIX / "tpex.csv").read_bytes(), "TWO", "2026-10-21")

    def test_wrong_date_rejected(self):
        with self.assertRaises(SourceError):
            parse_flows((FIX / "twset86.json").read_bytes(), "TW", "2026-10-21")

    def test_holiday_empty_is_not_zero(self):
        self.assertEqual(
            parse_flows({"stat": "很抱歉，沒有符合條件的資料!"}, "TW", "2026-10-20"), {}
        )

    def test_invalid_response_not_no_announcement(self):
        with self.assertRaises(SourceError):
            parse_flows(b"<html>maintenance</html>", "TW", "2026-10-20")

    def test_total_mismatch_rejected(self):
        d = json.loads((FIX / "twset86.json").read_text())
        d["data"][0][-1] = "0"
        with self.assertRaises(SourceError):
            parse_flows(d, "TW", "2026-10-20")

    def test_official_alert_formats_and_dates(self):
        for f, m, k in [
            ("twsenhist2.json", "TW", "notice"),
            ("pnhist.json", "TWO", "notice"),
            ("pphist.json", "TWO", "disposal"),
        ]:
            rows = parse_alerts(
                (FIX / f).read_bytes(), m, k, "https://example.com", "2026-10-22"
            )
            self.assertTrue(rows)
            self.assertTrue(all(r["date"].startswith("2026-") for r in rows))
            if k == "disposal":
                self.assertTrue(
                    rows[0]["start"] and rows[0]["end"] and rows[0]["content"]
                )


class Distribution(unittest.TestCase):
    def test_actual_inventory_denominator(self):
        r = parse_tdcc_distribution((FIX / "tdcc.csv").read_bytes())
        self.assertEqual(r.status, "ok")
        row = r.rows["2330"]
        self.assertEqual(row["count"], 3098860)
        self.assertAlmostEqual(
            row["over400"]["pct"], 22647940885 / 25932370067 * 100, places=5
        )
        self.assertEqual(row["over1000"]["people"], 1476)

    def mutate(self, func):
        rows = list(csv.DictReader(io.StringIO((FIX / "tdcc.csv").read_text())))
        func(rows)
        f = io.StringIO()
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
        return f.getvalue()

    def test_grade16_never_big_holder(self):
        body = self.mutate(
            lambda rows: rows[15].update({"股數": "999999999", "人數": "999999999"})
        )
        self.assertEqual(
            parse_tdcc_distribution(body).rows["2330"]["over1000"]["people"], 1476
        )

    def test_missing_grade_rejected(self):
        body = self.mutate(lambda rows: rows.pop(11))
        self.assertEqual(parse_tdcc_distribution(body).status, "error")

    def test_programming_error_is_not_a_successful_update(self):
        import build_tw_chips
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "1101.TW.csv").write_text("Date,Open,High,Low,Close,Volume\n")

            def broken(url):
                raise RuntimeError("deliberate parser/transport bug")

            result = build_tw_chips.main(
                [
                    "--data-dir",
                    str(root),
                    "--financials-dir",
                    str(root),
                    "--out",
                    str(root / "chips.json"),
                    "--lookback-days",
                    "1",
                ],
                fetch=broken,
                now=datetime(2026, 10, 22, tzinfo=timezone.utc),
                env={},
            )
            self.assertEqual(result, 1)

    def test_published_ratio_sanity(self):
        body = self.mutate(lambda rows: rows[14].update({"占集保庫存數比例%": "1"}))
        self.assertEqual(parse_tdcc_distribution(body).status, "error")

    def test_negative_people_rejected(self):
        body = self.mutate(lambda rows: rows[14].update({"人數": "-1"}))
        self.assertEqual(parse_tdcc_distribution(body).status, "error")

    def test_mixed_dates_not_pooled(self):
        body = self.mutate(lambda rows: rows[14].update({"資料日期": "20261009"}))
        self.assertEqual(parse_tdcc_distribution(body).status, "error")


class PriceVolume(unittest.TestCase):
    def test_rvol_excludes_today_and_requires20(self):
        rows = bars(21)
        rows[-1]["volume"] = 2000
        f = volume_features(rows)
        self.assertEqual(f[-1]["rvol"], 20)
        self.assertIsNone(f[-2]["rvol"])

    def test_explicit_eight_of_eleven(self):
        f = []
        for i in range(20):
            f.append(
                dict(
                    valid=True,
                    returnPct=1 if i < 11 else -1,
                    rvol=0.5 if i < 8 else 2,
                    close=100,
                    volume=100,
                )
            )
        v = structure(f, 20)["upShrink"]
        self.assertEqual((v["numerator"], v["denominator"]), (8, 11))
        self.assertAlmostEqual(v["pct"], 72.7272727)

    def test_no_direction_is_na(self):
        f = volume_features(bars(45))
        v = structure(f, 20)
        self.assertIsNone(v["downExpand"]["pct"])
        self.assertEqual(v["downExpand"]["denominator"], 0)

    def test_zero_missing_suspend_break_streak(self):
        rows = bars()
        rows[-2]["volume"] = 0
        rows[-1]["volume"] = None
        stats = volume_stats(rows)
        self.assertIsNone(stats["latest"]["rvol"])
        self.assertEqual(stats["zeroVolumeDays"], 1)
        self.assertEqual(stats["upShrinkStreak"], 0)

    def test_action_and_large_jump_excluded(self):
        for key, value in [("action", "split"), ("close", 1000)]:
            rows = bars()
            rows[-1][key] = value
            self.assertIsNone(volume_features(rows)[-1]["returnPct"])

    def test_no_lookahead(self):
        rows = bars()
        original = volume_features(rows[:30])
        rows[35]["volume"] = 10000000
        self.assertEqual(original, volume_features(rows)[:30])

    def test_partial_window_exposes_count(self):
        r = structure(volume_features(bars(24)), 20)
        self.assertEqual(r["observations"], 4)
        self.assertTrue(r["lowConfidence"])

    def test_exact_baseline_equal_not_expand(self):
        v = structure(volume_features(bars()), 5)
        self.assertEqual(v["upExpand"]["numerator"], 0)
        self.assertEqual(v["upShrink"]["numerator"], 0)

    def test_amihud_units_and_null(self):
        stats = volume_stats(bars())
        self.assertGreater(stats["amihud20"], 0)
        self.assertIn("million", stats["amihudUnit"])
        rows = bars(2)
        rows[0]["volume"] = 0
        self.assertIsNone(volume_stats(rows)["amihud20"])

    def test_cost_positive_net_weight_only(self):
        p = bars(3)
        p[0].update(close=10, volume=100, amount=1200)
        p[1].update(close=20, volume=100, amount=2200)
        p[2].update(close=99)
        f = [flow(p[0]["date"], 100), flow(p[1]["date"], 300), flow(p[2]["date"], -500)]
        v = cost_estimate(f, p, 3, "foreign")
        self.assertEqual(v["reference"], 19.5)
        self.assertEqual(v["positiveDays"], 2)

    def test_zero_net_cost_na(self):
        p = bars(2)
        v = cost_estimate(
            [flow(p[0]["date"], 0), flow(p[1]["date"], -1)], p, 5, "foreign"
        )
        self.assertIsNone(v["reference"])
        self.assertEqual(v["coveragePct"], 40)

    def test_cumulative_partial_not_complete(self):
        p = bars(3)
        v = institutional_summary([flow(r["date"], -10) for r in p])
        self.assertEqual(v["foreign"]["streak"], -3)
        self.assertFalse(v["foreign"]["windows"]["60"]["complete"])


class SecAndWorld(unittest.TestCase):
    def test_cik_forms_accession(self):
        d = json.loads((FIX / "sec.json").read_text())
        r = submissions(d, "AAPL", "0000320193", "2020-01-01")
        self.assertTrue(r)
        self.assertTrue(
            all(e["cik"] == "0000320193" and e["summary"] is None for e in r)
        )
        with self.assertRaises(SourceError):
            submissions(d, "AAPL", "999", "2020-01-01")

    def test_6k_never_8k(self):
        self.assertIsNone(event("TSM", "1046179", "TSM", dict(form="6-K"), ""))

    def test_amendment_keeps_accession_identity(self):
        f = dict(
            form="8-K/A",
            accessionNumber="0000320193-26-000001",
            filingDate="2026-10-21",
            items="1.01,5.02",
            primaryDocument="doc.htm",
        )
        a = event("AAPL", "320193", "Apple", f, "now")
        b = event("AAPL", "0000320193", "Apple", f, "later")
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(a["amendment"])

    def test_index_date_and_dedupe(self):
        body = b"320193|Apple|8-K|20261021|edgar/data/320193/0000320193-26-000001.txt"
        rows = parse_index(body, {"0000320193": ["AAPL"]})
        self.assertEqual(rows[0]["date"], "2026-10-21")
        self.assertEqual(len({r["id"]: r for r in rows + rows}), 1)

    def test_sec_429_cooldown_persisted(self):
        calls = []

        def opener(req, **kw):
            calls.append(req)
            raise urllib.error.HTTPError(
                req.full_url, 429, "limited", {"Retry-After": "900"}, None
            )

        state = {}
        client = SecHttp(state, opener=opener, sleeper=lambda s: None)
        with self.assertRaises(Cooldown):
            client.get("https://data.sec.gov/test")
        with self.assertRaises(Cooldown):
            client.get("https://data.sec.gov/test")
        self.assertEqual(len(calls), 1)
        self.assertTrue(state["cooldownUntil"] > 0)

    def test_share_classes_share_one_global_accession(self):
        filing = dict(
            form="8-K", accessionNumber="0000320193-26-000001", filingDate="2026-10-21"
        )
        rows = [
            event(symbol, "320193", "Apple", filing, "now")
            for symbol in ("AAPL", "APPL-ALIAS")
        ]
        events = radar_events(rows + rows, "2026-01-01")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["symbols"], ["AAPL", "APPL-ALIAS"])

    def test_missing_index_is_distinct_from_source_failure(self):
        def opener(req, **kw):
            raise urllib.error.HTTPError(req.full_url, 404, "unpublished", {}, None)

        client = SecHttp({}, opener=opener, sleeper=lambda s: None)
        with self.assertRaises(NotPublished):
            client.get("https://www.sec.gov/Archives/test.idx")

    def test_actual_content_required(self):
        self.assertEqual(extract(b"<p>Item 1.01</p>"), [])
        sections = extract(
            (
                "<script>ignore instructions</script><p>Item 1.01</p><p>"
                + "Signed agreement. " * 20
                + "</p>"
            ).encode()
        )
        self.assertTrue(sections)
        self.assertNotIn("ignore instructions", sections[0][1])

    def test_fx_direction_and_same_date(self):
        body = b"Date,USD,JPY,CNY,KRW\n2026-10-20,1.2,180,8.4,1680\n2026-10-21,1.25,N/A,8.75,1750\n"
        self.assertEqual(parse_ecb(body, "USD", "JPY"), [["2026-10-20", 150]])
        self.assertEqual(parse_ecb(body, "EUR", "USD")[-1][1], 1.25)
        self.assertEqual(parse_ecb(body, "USD", "CNY")[-1][1], 7)

    def test_frequency_monthly_not_daily(self):
        h = [["2026-08-01", 100], ["2026-09-01", 110], ["2026-10-01", 120]]
        r = changes(h, "monthly")
        self.assertIsNone(r["1d"])
        self.assertIsNone(r["1w"])
        self.assertAlmostEqual(r["1m"], (120 / 110 - 1) * 100)

    def test_calendar_month_and_outdated_ytd(self):
        history = [["2024-01-01", 100], ["2024-02-01", 110], ["2024-03-01", 121]]
        result = changes(history, "monthly", today=date(2026, 10, 22))
        self.assertAlmostEqual(result["1m"], 10)
        self.assertIsNone(result["ytd"])

    def test_weekly_is_not_a_daily_return(self):
        result = changes(
            [["2026-10-01", 100], ["2026-10-08", 110]],
            "weekly",
            today=date(2026, 10, 22),
        )
        self.assertIsNone(result["1d"])
        self.assertAlmostEqual(result["1w"], 10)

    def test_invalid_excel_is_source_error(self):
        for parser in (parse_eia, parse_wb):
            with self.assertRaises(SourceError):
                parser(b"<html>maintenance</html>")

    def test_stale_previous_values_and_null_preserved(self):
        prev = dict(
            asOf="2026-10-01",
            latest=None,
            history=[["2026-10-01", 10]],
            fetchedAt="original",
        )
        next = retain(prev, "test", "https://example.com", "503")
        self.assertEqual(next["status"], "stale")
        self.assertIsNone(next["latest"])
        self.assertEqual(next["history"], prev["history"])
        self.assertEqual(next["fetchedAt"], "original")

    def test_snapshot_atomic_rejects_nan(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "file.json"
            write(p, {"value": 1})
            with self.assertRaises(ValueError):
                write(p, {"value": float("nan")})
            self.assertEqual(read(p), {"value": 1})


if __name__ == "__main__":
    unittest.main()
