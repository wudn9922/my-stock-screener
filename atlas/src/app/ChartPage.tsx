import { App, type ChartWorkspaceProps } from './App';
import { LegacyImportCard } from '../ui/LegacyImportCard';

/** Lazy route entry for the chart workspace (engine, drawings, panels and their styles). */
export default function ChartPage(props: ChartWorkspaceProps) {
  return (
    <div className="chart-page-layout">
      {props.active !== false && <LegacyImportCard />}
      <App {...props} />
    </div>
  );
}
