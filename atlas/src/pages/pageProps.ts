import type { ReportState } from '../report/useReport';
import type { Route } from '../app/routes';
import type { OpenChartOptions } from '../app/Site';

export interface PageProps {
  report: ReportState;
  reload: () => void;
  navigate: (route: Route, options?: { replace?: boolean }) => void;
  openChart: (options: OpenChartOptions) => void;
}
