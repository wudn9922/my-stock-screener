import { afterEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LegacyImportCard } from '../src/ui/LegacyImportCard';
import { accessState } from '../src/auth/access';
import { legacyImportState, type LegacyImportSnapshot } from '../src/storage/legacyImport';
afterEach(() => vi.restoreAllMocks());
function state(status: LegacyImportSnapshot['status']) {
  vi.spyOn(accessState, 'getSnapshot').mockReturnValue({ ...accessState.getSnapshot(), ready: true, userId: `U${'0'.repeat(32)}`, storageMode: 'device' });
  vi.spyOn(legacyImportState, 'getSnapshot').mockReturnValue({ ...legacyImportState.getSnapshot(), status, counts: { drawings: 1, indicators: 0, lists: 0, settings: 0 } });
}
it.each(['idle', 'checking', 'dismissed', 'empty'] as const)('renders nothing for %s, including no empty-device notice', status => {
  state(status);
  expect(renderToStaticMarkup(<LegacyImportCard />)).toBe('');
});
it('preserves the explicit import option for an authorized account with legacy records', () => {
  state('available');
  const html = renderToStaticMarkup(<LegacyImportCard />);
  expect(html).toContain('legacy-import-confirm');
  expect(html).toContain('畫線 1');
  expect(html).not.toContain('沒有可匯入');
});
