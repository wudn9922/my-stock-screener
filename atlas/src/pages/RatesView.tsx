import type { PageProps } from './pageProps';
import { PageState } from '../ui/PageState';

export type RatesViewProps = Pick<PageProps, 'navigate' | 'openChart'>;

/** Placeholder: replaced by the rates research view (rates.json / rates-history.json). */
export default function RatesView(_props: RatesViewProps) {
  return <PageState kind="empty" title="資料尚未產生" message="美債與利率資料還沒有產生，請稍後再試。" />;
}
