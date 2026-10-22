/** One persistent, authenticated drawing store across the main and embedded charts. */
import { AppStore } from "./AppStore";
import { SCREENER_HOSTING } from "./HostingMode";
import { startDrawingSync } from "../cloud/startDrawingSync";
export const chartStore = new AppStore();
let ready: Promise<void> | undefined;
export function initializeChartStore() {
  ready ??= chartStore.initialize().then(() => {
    const provider = chartStore.getSnapshot().app.provider;
    if (SCREENER_HOSTING && provider !== "market" && provider !== "demo")
      chartStore.updateApp({ provider: "market" });
    if (SCREENER_HOSTING) void startDrawingSync(chartStore);
  });
  return ready;
}
