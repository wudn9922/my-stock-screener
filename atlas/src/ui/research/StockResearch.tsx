import { VolumePanel } from "./VolumePanel";
import { OfficialAlerts } from "./OfficialAlerts";
import { SecRadar } from "./SecRadar";
export default function StockResearch({
  symbol,
  simulated = false,
  mode = "all",
}: {
  symbol: string;
  simulated?: boolean;
  mode?: "all" | "volume" | "official";
}) {
  const tw = /\.TWO?$/.test(symbol);
  return (
    <div>
      {simulated && (
        <p className="research-warning">
          研究面板使用官方或日報真實資料；目前圖表是模擬資料，兩者不可直接比較。
        </p>
      )}
      {mode !== "official" && <VolumePanel key={`volume-${symbol}`} symbol={symbol} inline={mode === "volume"} />}
      {mode !== "volume" && <div className="stock-official-notices">
        {tw ? <OfficialAlerts symbol={symbol} /> : <SecRadar symbol={symbol} />}
      </div>}
    </div>
  );
}
