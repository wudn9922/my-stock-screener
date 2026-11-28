"""Verify workflow catalog link targets, not just displayed row names."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
CATALOG = ROOT / 'docs' / 'WORKFLOWS.md'
LINK = re.compile(r'\[([^\]\n]+)\]\(\.\./\.github/workflows/([^\)\n]+)\)')


class WorkflowDocsTest(unittest.TestCase):
    def test_workflow_links_match_files(self):
        expected = {path.name for path in WORKFLOWS.glob('*.yml')}
        links = LINK.findall(CATALOG.read_text(encoding='utf-8'))
        actual = {target for label, target in links if label == f'`{target}`'}
        missing = sorted(expected - actual)
        extra = sorted({target for _, target in links if not (WORKFLOWS / target).is_file() or target not in expected})
        mismatched = sorted(f'{label} -> {target}' for label, target in links if label != f'`{target}`')
        self.assertFalse(missing or extra or mismatched,
                         f'Workflow links: missing={missing}; extra={extra}; mismatched={mismatched}')


if __name__ == '__main__':
    unittest.main()
