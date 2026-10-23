import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LegacyImportCard } from '../src/ui/LegacyImportCard';

const state = vi.hoisted(() => ({
  access: { ready: true, userId: 'U00000000000000000000000000000000' as string | null, storageMode: 'device', epoch: 1 },
  legacy: {
    status: 'available', counts: { drawings: 4, indicators: 3, lists: 2, settings: 5 },
    added: 0, conflicts: 0, error: null as string | null, warnings: [] as string[],
  },
}));
vi.mock('../src/auth/access', () => ({
  accessState: { subscribe: () => () => {}, getSnapshot: () => state.access },
}));
vi.mock('../src/storage/legacyImport', () => ({
  legacyImportState: { subscribe: () => () => {}, getSnapshot: () => state.legacy },
  dismissLegacyImport: vi.fn(), ensureLegacyImport: vi.fn(), importLegacyData: vi.fn(), showLegacyImportAgain: vi.fn(),
}));

beforeEach(() => {
  state.access = { ready: true, userId: 'U00000000000000000000000000000000', storageMode: 'device', epoch: 1 };
  state.legacy = {
    status: 'available', counts: { drawings: 4, indicators: 3, lists: 2, settings: 5 },
    added: 0, conflicts: 0, error: null, warnings: [],
  };
});
describe('legacy import card', () => {
  it.each([
    { ready: true, userId: null, storageMode: 'none', epoch: 1 },
    { ready: true, userId: 'U00000000000000000000000000000000', storageMode: 'none', epoch: 1 },
    { ready: false, userId: 'U00000000000000000000000000000000', storageMode: 'device', epoch: 1 },
  ])('hides an old available result when the live access is not allowed: %j', access => {
    state.access = access;
    expect(renderToStaticMarkup(<LegacyImportCard />)).toBe('');
  });
  it.each(['device', 'cloud'])('shows actual counts and explicit choices for %s', storageMode => {
    state.access.storageMode = storageMode;
    const html = renderToStaticMarkup(<LegacyImportCard />);
    expect(html).toContain('畫線 4、指標 3、清單 2、設定 5');
    expect(html).toContain('data-testid="legacy-import-confirm"');
    expect(html).toContain('data-testid="legacy-import-dismiss"');
    expect(html).toContain('舊版資料維持原樣');
  });
  it('reports additions and account-winning conflicts after import', () => {
    state.legacy = { ...state.legacy, status: 'imported', added: 9, conflicts: 2 };
    const html = renderToStaticMarkup(<LegacyImportCard />);
    expect(html).toContain('已匯入 9 項；2 項衝突保留帳號版本');
    expect(html).not.toContain('data-testid="legacy-import-confirm"');
  });
  it('renders failure and source warnings as escaped text with a retry', () => {
    state.legacy = { ...state.legacy, status: 'error', error: '<script>untrusted</script>', warnings: ['部分資料尚未讀取'] };
    const html = renderToStaticMarkup(<LegacyImportCard />);
    expect(html).toContain('&lt;script&gt;untrusted&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('重新檢查舊版資料');
    expect(html).toContain('部分資料尚未讀取');
  });
  it.each(['idle', 'checking', 'dismissed'])('keeps the chart clear during %s', status => {
    state.legacy.status = status;
    expect(renderToStaticMarkup(<LegacyImportCard />)).toBe('');
  });
});
