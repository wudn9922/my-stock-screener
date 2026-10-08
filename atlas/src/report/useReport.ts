import { useCallback, useEffect, useState } from 'react';
import { loadReport, type ReportLoadResult } from './loadReport';

export type ReportState = { status: 'loading' } | ReportLoadResult;

/** Shared daily report for all pages; `reload` re-fetches after a failure. */
export function useReport() {
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
  useEffect(() => run(false), [run]);
  const reload = useCallback(() => {
    run(true);
  }, [run]);
  return { state, reload };
}
