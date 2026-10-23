"""Offline basis veto, stored-cell proof, provenance and daily refresh regression."""
import copy
import gzip
import json
import sys
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'atlas/scripts'))
import backfill_yahoo_annual as backfill
import build_financials as fin
import seed_financials_sec as sec
import build_financials_tw_finmind as tw

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def document(src='sec'):
    return {'version': 1, 'symbol': 'TEST', 'market': 'US', 'currency': 'USD',
            'foreignReporter': False, 'fyEndMonth': 12, 'source': 'SEC EDGAR',
            'annual': {'end': ['2020-12-31', '2024-12-31', '2026-12-31'], 'fy': [2020, 2024, 2026],
                       'src': [src] * 3, 'secEnd': [None, '2024-12-29', None],
                       'values': {'revenue': [100, 100, 100], 'cash': [None, None, None],
                                  'epsBasic': [None, 2, None]}},
            'quarterly': {'end': ['2024-12-31'], 'fy': [2024], 'fq': [4], 'src': ['sec'],
                          'values': {'revenue': [25], 'epsBasic': [0.5]}},
            'quality': {'level': 'partial', 'issues': ['q4-derived', 'debt-approx', 'eps-inconsistent',
                        'financial-sector-format', 'future-unknown'],
                        'revisions': [{'kind': 'future-kind', 'end': '2024-12-31', 'raw': {'x': 1}}]}}


def source(**values):
    return {'currency': 'USD', 'market': 'US', 'annual': {
        '2020-12-31': {'revenue': 100, 'cash': 9},
        '2024-12-31': {'revenue': 100, 'cash': 40, 'epsBasic': 2, 'epsDiluted': 2, 'dilutedShares': 50, **values},
        '2026-12-31': {'revenue': 100, 'cash': 9}}}


def payload(values):
    kinds = {field: fin.FIELDS[field][0] for field in values}
    return {'timeseries': {'result': [
        {'meta': {'type': ['annual' + kinds[field]]}, 'annual' + kinds[field]: [
            {'asOfDate': '2024-12-31', 'periodType': '12M', 'currencyCode': 'USD', 'reportedValue': {'raw': value}}]}
        for field, value in values.items()]}}


class AnnualBackfill(unittest.TestCase):
    def test_only_nulls_in_existing_in_scope_rows(self):
        before = document()
        after, fills, skipped = backfill.backfill_document(before, source())
        self.assertEqual([f['field'] for f in fills], ['cash'])
        self.assertEqual(after['annual']['values']['cash'], [None, 40, None])
        self.assertEqual(after['quarterly'], before['quarterly'])
        self.assertEqual(after['annual']['secEnd'], before['annual']['secEnd'])
        self.assertEqual(backfill.validate(before, after), [('TEST', '2024-12-31', 'cash', None, 40)])
        self.assertEqual(before, document())

    def test_any_overlap_vetoes_whole_period_including_eps(self):
        for field, value in [('revenue', 103), ('epsBasic', 2.05)]:
            with self.subTest(field=field):
                after, fills, skipped = backfill.backfill_document(document(), source(**{field: value}))
                self.assertFalse(fills)
                self.assertEqual(after, document())
                self.assertEqual(skipped[0]['reason'], 'unexplained')

    def test_exact_two_percent_allowed_and_zero_negative_safe(self):
        self.assertFalse(backfill.conflicts({'a': 100, 'b': -100, 'z': 0}, {'a': 102, 'b': -102, 'z': 0}))
        self.assertEqual(len(backfill.conflicts({'a': 0, 'b': -100}, {'a': 0.01, 'b': 100})), 2)

    def test_unit_mismatch_has_evidence_and_no_fills(self):
        for value in [1000, 100000, 0.1]:
            after, fills, skipped = backfill.backfill_document(document(), source(revenue=value))
            self.assertFalse(fills)
            self.assertEqual(skipped[0]['reason'], 'unit-mismatch')
            self.assertTrue(skipped[0]['differences'])

    def test_restated_requires_matching_later_sec_comparative(self):
        doc = document()
        doc['quality']['revisions'].append({'kind': 'restated', 'period': 'annual', 'end': '2024-12-31',
            'field': 'revenue', 'old': 100, 'new': 120, 'accn': 'new', 'originalAccn': 'old',
            'filed': '2026-02-01', 'originalFiled': '2025-02-01'})
        _, fills, skipped = backfill.backfill_document(doc, source(revenue=120))
        self.assertFalse(fills)
        self.assertEqual(skipped[0]['reason'], 'restated')
        self.assertEqual(skipped[0]['secEvidence'][0]['accn'], 'new')

    def test_restated_and_mixed_basis_rows_have_no_exception(self):
        doc = document()
        doc['quality']['issues'] += ['restated', 'mixed-basis']
        doc['annual']['secRestatementIssues'] = [None, ['restated', 'mixed-basis'], None]
        _, fills, _ = backfill.backfill_document(doc, source(revenue=120))
        self.assertFalse(fills)
        _, fills, skipped = backfill.backfill_document(doc, source())
        self.assertFalse(fills)
        self.assertEqual(skipped[0]['reason'], 'basis-unverified')

    def test_no_overlap_cannot_establish_common_basis(self):
        _, fills, skipped = backfill.backfill_document(document(), {'annual': {'2024-12-31': {'equity': 5}}})
        self.assertFalse(fills)
        self.assertEqual(skipped[0]['reason'], 'no-overlap')

    def test_ambiguous_dates_never_choose_arbitrary_row(self):
        incoming = source()
        incoming['annual']['2024-12-29'] = incoming['annual']['2024-12-31']
        _, fills, skipped = backfill.backfill_document(document(), incoming)
        self.assertFalse(fills)
        self.assertEqual(skipped[0]['reason'], 'ambiguous-period')

    def test_no_new_annual_rows(self):
        incoming = source()
        incoming['annual']['2023-12-31'] = {'cash': 100}
        after, _, _ = backfill.backfill_document(document(), incoming)
        self.assertEqual(after['annual']['end'], document()['annual']['end'])

    def test_all_flags_revisions_and_row_source_kept_with_field_provenance(self):
        for src in ['sec', 'finmind', 'yahoo']:
            doc = document(src)
            after, _, _ = backfill.backfill_document(doc, source())
            self.assertTrue(set(doc['quality']['issues']) <= set(after['quality']['issues']))
            self.assertEqual(after['quality']['revisions'], doc['quality']['revisions'])
            self.assertEqual(after['annual']['src'], doc['annual']['src'])
            self.assertEqual(after['annual']['fieldSrc'][1], {'cash': 'yahoo'})
            self.assertIsNone(after['annual']['values'].get('epsDiluted'))
            periods, extra = fin.read_section(after['annual'])
            fin.assign_fiscal_labels(periods, [], 12)
            self.assertEqual(fin.write_section(periods, extra, False)['fieldSrc'], after['annual']['fieldSrc'])

    def test_quality_called_only_through_builder(self):
        with patch.object(fin, 'quality', wraps=fin.quality) as quality:
            backfill.backfill_document(document(), source())
            quality.assert_called_once()

    def test_daily_yahoo_refresh_keeps_fills_and_history_without_churn(self):
        for src in ['sec', 'finmind', 'yahoo']:
            doc, _, _ = backfill.backfill_document(document(src), source())
            for values in [{'revenue': 100}, {'revenue': 500, 'cash': 800}, {'revenue': 100, 'cash': 40}, {'equity': 77}]:
                with self.subTest(src=src, values=values):
                    refreshed, _ = fin.update_document(doc, payload(values), 'TEST', NOW)
                    i = refreshed['annual']['end'].index('2024-12-31')
                    self.assertEqual(refreshed['annual']['values']['cash'][i], 40)
                    self.assertEqual(refreshed['annual']['values']['revenue'][i], 100)
                    self.assertIsNone(refreshed['annual']['values'].get('equity', [None] * 3)[i])
                    self.assertEqual(refreshed['annual']['fieldSrc'][i]['cash'], 'yahoo')
                    self.assertTrue(set(doc['quality']['issues']) <= set(refreshed['quality']['issues']))
                    self.assertTrue(all(r in refreshed['quality']['revisions'] for r in doc['quality']['revisions']))
                    again, _ = fin.update_document(refreshed, payload(values), 'TEST', NOW)
                    self.assertEqual(again, refreshed)

    def test_sec_refresh_retains_absent_yahoo_fill_but_attributes_sec_replacement(self):
        doc, _, _ = backfill.backfill_document(document(), source())
        old, _ = fin.read_section(doc['annual'])
        for values, src in [({'revenue': 100}, 'yahoo'), ({'revenue': 100, 'cash': 42}, 'sec')]:
            fresh = fin.period('2024-12-31', values, 'sec')
            periods = sec._merge_section(old, [fresh], 'annual', '2026-10-23')[0]
            row = next(p for p in periods if p['end'] == '2024-12-31')
            self.assertEqual(row['values']['cash'], 40 if src == 'yahoo' else 42)
            self.assertEqual(row['extra']['fieldSrc']['cash'], src)

    def test_finmind_refresh_keeps_field_sources_and_all_history(self):
        doc = document('finmind')
        doc.update(symbol='2330.TW', market='TW', currency='TWD')
        doc, _, _ = backfill.backfill_document(doc, source())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '2330.TW.json'
            path.write_bytes(backfill.encode(doc))
            annual = [fin.period('2024-12-31', {'revenue': 100}, 'finmind')]
            self.assertEqual(tw.write_stock(tmp, '2330.TW', annual, [], set(), NOW, NOW.date()), 'written')
            refreshed = json.loads(path.read_bytes())
            i = refreshed['annual']['end'].index('2024-12-31')
            self.assertEqual(refreshed['annual']['values']['cash'][i], 40)
            self.assertEqual(refreshed['annual']['fieldSrc'][i]['cash'], 'yahoo')
            self.assertTrue(set(doc['quality']['issues']) <= set(refreshed['quality']['issues']))
            self.assertTrue(all(r in refreshed['quality']['revisions'] for r in doc['quality']['revisions']))
            before = path.read_bytes()
            self.assertEqual(tw.write_stock(tmp, '2330.TW', [fin.period('2024-12-31', {'revenue': 100}, 'finmind')], [], set(), NOW, NOW.date()), 'unchanged')
            self.assertEqual(path.read_bytes(), before)

    def test_validator_rejects_nonnull_quarterly_flag_revision_and_eps_changes(self):
        good, _, _ = backfill.backfill_document(document(), source())
        for mutation in [lambda d: d['annual']['values']['revenue'].__setitem__(1, 101),
                         lambda d: d['quarterly']['values']['revenue'].__setitem__(0, 99),
                         lambda d: d['quality']['issues'].remove('q4-derived'),
                         lambda d: d['quality']['revisions'].clear(),
                         lambda d: d['annual']['values'].__setitem__('epsDiluted', [None, 2, None])]:
            changed = copy.deepcopy(good)
            mutation(changed)
            with self.assertRaises(ValueError):
                backfill.validate(document(), changed)

    def test_fetch_snapshot_captures_every_overlap_field_and_both_windows(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'financials'
            directory.mkdir()
            (directory / 'TEST.json').write_bytes(backfill.encode(document()))
            client = Mock()
            client.get_json.return_value = payload({'revenue': 100, 'epsBasic': 2, 'cash': 40})
            snapshot = Path(tmp) / 'source.gz'
            with patch.object(fin, 'connect_yahoo', return_value=client), patch.object(backfill.time, 'sleep'):
                backfill.fetch_sources(directory, snapshot, workers=1)
            raw = json.loads(gzip.decompress(snapshot.read_bytes()))
            self.assertFalse(raw['errors'])
            row = raw['symbols']['TEST']
            self.assertEqual(row['annual']['2024-12-31'], {'revenue': 100, 'epsBasic': 2, 'cash': 40})
            self.assertEqual(len(row['requests']), 2)
            self.assertTrue(all(r['fetchedAt'] and r['payloadSha256'] for r in row['requests']))
            self.assertTrue(all('quarterly' not in call.args[1]['type'] for call in client.get_json.call_args_list))

    def test_audit_write_failure_rolls_back_permanent_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'financials'
            directory.mkdir()
            path = directory / 'TEST.json'
            path.write_bytes(backfill.encode(document()))
            before = path.read_bytes()
            snapshot, audit = Path(tmp) / 'source.gz', Path(tmp) / 'audit.json'
            snapshot.write_bytes(gzip.compress(backfill.encode({'symbols': {'TEST': source()}, 'errors': {}}), mtime=0))
            real_write = fin.write_atomic
            def write(target, content):
                if Path(target) == audit:
                    raise OSError('audit unavailable')
                return real_write(target, content)
            with patch.object(fin, 'write_atomic', side_effect=write), self.assertRaises(OSError):
                backfill.run(directory, snapshot, audit, True)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(audit.exists())

    def test_full_git_tree_proof_rejects_unlogged_and_protected_edits(self):
        import build_growth as growth
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            report = repo / 'docs/report'
            directory = report / 'financials'
            directory.mkdir(parents=True)
            (report / 'fundamentals').mkdir()
            (report / 'fundamentals/.keep').write_text('')
            (report / 'q4-8k').mkdir()
            (report / 'q4-8k/answers.json').write_text('{}')
            for name in ['q4-eps-missing-classification.json', 'q4-eps-missing-classification.md',
                         'q4-eps-acceptance-ledger.json', 'q4-eps-acceptance-ledger.md',
                         'sec-restated-comparatives.json', 'sec-restated-comparatives-apply.json']:
                (report / name).write_text('{}')
            doc = document()
            (directory / 'TEST.json').write_bytes(backfill.encode(doc))
            (directory / 'index.json').write_text('{}')
            (report / 'universe.json').write_text('{}')
            (report / 'growth.json').write_bytes(backfill.encode({'fields': growth.FIELDS, 'rows': []}))
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                            'commit', '-qm', 'baseline'], check=True)
            snapshot = repo / 'snapshot.gz'
            snapshot.write_bytes(gzip.compress(backfill.encode({'symbols': {'TEST': source()}, 'errors': {}}), mtime=0))
            audit = repo / 'audit.json'
            backfill.run(directory, snapshot, audit, True)
            with patch.object(backfill, 'ROOT', repo):
                proof = backfill.verify_against('HEAD', directory, snapshot, audit)
                self.assertTrue(proof['exactReplay'])
                self.assertEqual(proof['filledCells'], 1)
                (report / 'q4-8k/answers.json').write_text('{"changed":true}')
                with self.assertRaisesRegex(ValueError, 'Q4 protected'):
                    backfill.verify_against('HEAD', directory, snapshot, audit)
                (report / 'q4-8k/answers.json').write_text('{}')
                tampered = json.loads((directory / 'TEST.json').read_bytes())
                tampered['annual']['secEnd'][1] = '2024-12-30'
                (directory / 'TEST.json').write_bytes(backfill.encode(tampered))
                with self.assertRaisesRegex(ValueError, 'metadata changed'):
                    backfill.verify_against('HEAD', directory, snapshot, audit)

    def test_dry_run_replay_exact_apply_and_second_run_no_value_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'TEST.json'
            path.write_bytes(backfill.encode(document()))
            snapshot = root / 'snapshot.gz'
            snapshot.write_bytes(gzip.compress(backfill.encode({'symbols': {'TEST': source()}, 'errors': {}}), mtime=0))
            audit = root / 'audit.json'
            before = path.read_bytes()
            dry = backfill.run(root, snapshot, audit)
            self.assertEqual(path.read_bytes(), before)
            # Keep audit outside financials in real use; this fixture removes it to avoid treating it as a symbol.
            audit.unlink()
            snapshot.rename(root / 'snapshot.gz')
            applied = backfill.run(root, snapshot, audit, True)
            self.assertEqual(dry['fills'], applied['fills'])
            receipt = audit.read_bytes()
            self.assertEqual(backfill.run(root, snapshot, audit, True)['fills'], applied['fills'])
            self.assertEqual(audit.read_bytes(), receipt)
            audit.unlink()
            after = path.read_bytes()
            repeat = backfill.run(root, snapshot, audit, True)
            self.assertEqual(path.read_bytes(), after)
            self.assertEqual(repeat['summary']['fills'], 0)


if __name__ == '__main__':
    unittest.main()
