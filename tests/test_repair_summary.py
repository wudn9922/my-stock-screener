"""Repair run summary (log + GitHub step summary) built from the committed dry-run reports."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
import repair_kline_archive as repair

REPORTS = ROOT/'docs/research-verification'


def load(name):
    return json.loads((REPORTS/name).read_text())


def section(sections, prefix):
    return next((headers, rows) for title, headers, rows in sections if title.startswith(prefix))


def table(sections, prefix):
    headers, rows = section(sections, prefix)
    return {r[0]: dict(zip(headers[1:], r[1:])) for r in rows}


class BackfillSummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = load('official-backfill-dry-run-20261107.json')
        cls.sections = repair.summary_sections(cls.report)

    def test_common_fields_and_validation(self):
        common = {k: v for k, v in section(self.sections, 'Official archive repair')[1]}
        self.assertEqual(common, {'mode': 'backfill', 'apply': 'false', 'status': 'dry-run',
                                  'archive commit': '62bd86a6e7fcde3572c56aa607eed5018a0cfe68'})
        validation = table(self.sections, 'Validation')
        self.assertEqual(validation['rows'], dict(before='9,796,080', after='9,796,080', delta='+0'))
        self.assertEqual(validation['files']['before'], '41,573')

    def test_per_date_counts_match_the_report(self):
        counts = table(self.sections, 'Backfill per date')
        self.assertEqual(counts['rows to add'], {'2021-04-06': '1,727', '2025-08-01': '1,783', 'total': '3,510'})
        self.assertEqual(counts['of which factor != 1'], {'2021-04-06': '661', '2025-08-01': '216', 'total': '877'})
        self.assertEqual(counts['of which factor = 1']['2021-04-06'], '1,066')
        self.assertEqual(counts['rows skipped'], {'2021-04-06': '495', '2025-08-01': '247', 'total': '742'})
        self.assertEqual(counts['skipped: unstable-price-basis'], {'2021-04-06': '11', '2025-08-01': '14', 'total': '25'})
        self.assertEqual(counts['existing rows kept'], {'2021-04-06': '1', '2025-08-01': '193', 'total': '194'})
        for day in self.report['days']:
            self.assertEqual(sum(day['skippedByReason'].values()), len(day['skipped']))

    def test_samples_are_bounded_and_include_reference_symbols(self):
        headers, first = section(self.sections, 'Sample additions 2021-04-06')
        self.assertEqual(headers, ['symbol', 'open', 'high', 'low', 'close', 'volume', 'factor'])
        self.assertEqual(len(first), 10)
        self.assertEqual(first[0], ['2330.TW', '615.0000', '616.0000', '608.0000', '610.0000', '37664216', '1'])
        self.assertEqual(first[1][0], '^TWII')
        self.assertEqual(first[1][5], '-')  # index volume is unknown
        self.assertTrue(any(r[6] != '1' for r in first))
        _, second = section(self.sections, 'Sample additions 2025-08-01')
        self.assertEqual(second[0][0], '2330.TW')
        self.assertNotIn('^TWII', [r[0] for r in second])  # not added that day
        self.assertEqual(len({r[0] for r in second}), 10)

    def test_text_and_markdown_render_the_same_sections(self):
        text, markdown = repair.render_text(self.sections), repair.render_markdown(self.sections)
        self.assertIn('== Backfill per date ==', text)
        self.assertIn('### Backfill per date', markdown)
        self.assertIn('| rows to add | 1,727 | 1,783 | 3,510 |', markdown)
        self.assertIn('| 2330.TW | 615.0000 |', markdown)

    def test_summary_without_detail_rows_falls_back_to_counts(self):
        report = load('official-repair-rehearsal-backfill-20261107.json')
        counts = table(repair.summary_sections(report), 'Backfill per date')
        self.assertEqual(counts['rows to add']['total'], '3,510')
        self.assertEqual(table(repair.summary_sections(report), 'Validation')['rows']['delta'], '+3,510')


class RemovalAndRestoreSummaryTest(unittest.TestCase):
    def test_removal_dry_run(self):
        sections = repair.summary_sections(load('official-placeholder-removal-dry-run-20261107.json'))
        _, groups = section(sections, 'Placeholder rows matched')
        self.assertEqual(groups, [['TW', 'tpex', '2026-10-23', '894'], ['TW', 'twse', '2026-10-23', '1,320']])
        totals = dict(section(sections, 'Removal totals')[1])
        self.assertEqual(totals['matched rows'], '2,214')
        self.assertEqual(totals['expected rows'], '2,214')
        self.assertEqual(totals['difference'], '+0.00%')
        self.assertEqual(totals['year files touched'], '2,214')
        self.assertEqual(totals['backup written'], 'no')

    def test_removal_apply_reports_backup_and_count_difference(self):
        report = load('official-placeholder-removal-dry-run-20261107.json')
        report.update(apply=True, status='applied-locally', expected=2200, backupPath='/x/backup.json',
                      backupSha256='ab' * 32, changedFiles=4430)
        totals = dict(section(repair.summary_sections(report), 'Removal totals')[1])
        self.assertEqual(totals['difference'], '+0.64%')
        self.assertEqual(totals['backup written'], 'yes: /x/backup.json')
        self.assertEqual(totals['backup sha256'], 'ab' * 32)
        self.assertEqual(totals['archive files changed (incl. indexes, journal, manifest)'], '4,430')

    def test_restore(self):
        sections = repair.summary_sections(load('official-repair-rehearsal-restore-20261107.json'))
        restore = dict(section(sections, 'Restore')[1])
        self.assertEqual(restore, {'rows restored': '2,214', 'files touched': '4,430', 'byte exact': 'true'})

    def test_stopped_report_shows_error_and_missing_validation(self):
        report = dict(mode='remove-placeholders', apply=False, status='stopped', archiveCommit='c' * 40,
                      validationBefore=dict(files=3, rows=9), error='Placeholder count differs',
                      expected=2214, actual=0, rows=[], guardPassed=False)
        sections = repair.summary_sections(report)
        self.assertEqual(dict(section(sections, 'Official archive repair')[1])['error'], 'Placeholder count differs')
        self.assertEqual(table(sections, 'Validation')['rows'], dict(before='9', after='-', delta='-'))
        self.assertEqual(dict(section(sections, 'Removal totals')[1])['difference'], '-100.00%')


class EmitSummaryTest(unittest.TestCase):
    def test_prints_and_appends_to_step_summary(self):
        report = load('official-repair-rehearsal-restore-20261107.json')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'summary.md'
            path.write_text('existing\n')
            out = io.StringIO()
            repair.emit_summary(report, out, {'GITHUB_STEP_SUMMARY': str(path)})
            self.assertIn('rows restored  2,214', out.getvalue())
            body = path.read_text()
            self.assertTrue(body.startswith('existing\n### Official archive repair'))
            self.assertIn('| rows restored | 2,214 |', body)

    def test_without_step_summary_only_prints(self):
        out = io.StringIO()
        repair.emit_summary(load('official-repair-rehearsal-restore-20261107.json'), out, {})
        self.assertIn('== Restore ==', out.getvalue())


if __name__ == '__main__':
    unittest.main()
