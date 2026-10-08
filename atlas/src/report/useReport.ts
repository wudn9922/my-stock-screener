import { useCallback, useEffect, useState } from 'react';
import { loadReport, type ReportLoadResult } from './loadReport';

export type ReportState = { status: 'loading' } | ReportLoadResult;

/** Shared daily report for all pages; `reload` re-fetches after a failure. */
export function useReport(enabled = true) {
  const [state, setState] = useState<ReportState>({ status: 'loading' });
  const run = useCallback((refresh: boolean) => {
    let active = true;
    if (refresh) setState({ status: 'loading' });
    void loadReport(refresh).then((result) => {
      if (active) setState(result);
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => (enabled ? run(false) : undefined), [run, enabled]);
  const reload = useCallback(() => {
    run(true);
  }, [run]);
  return { state, reload };
}
