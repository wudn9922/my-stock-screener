import { App, type ChartWorkspaceProps } from './App';

/** Lazy route entry for the chart workspace (engine, drawings, panels and their styles). */
export default function ChartPage(props: ChartWorkspaceProps) {
  return <App {...props} />;
}
