import type { IpoCompany } from "../ipo/ipoWatch";
import { formatDateTime } from "./format";
export function IpoDetails({
  company,
  onBack,
}: {
  company: IpoCompany;
  onBack: () => void;
}) {
  return (
    <section className="ipo-details" aria-label="IPO 公司資料">
      <button type="button" className="text-button" onClick={onBack}>
        返回搜尋
      </button>
      <h3>
        {company.name} · {company.symbol || "代號待公布"}
      </h3>
      <p>
        <b>{company.status}</b>
      </p>
      <dl>
        <dt>擬上市市場</dt>
        <dd>{company.exchange || "待公布"}</dd>
        <dt>最近申報</dt>
        <dd>{company.filedDate ?? "未提供"}</dd>
        {company.expectedPricingDate && <><dt>預計定價（非掛牌日）</dt><dd>{company.expectedPricingDate}</dd></>}
        {company.pricingDate && <><dt>定價日（非掛牌日）</dt><dd>{company.pricingDate}</dd></>}
        <dt>預計掛牌</dt>
        <dd>{company.listingDate ?? "尚未公布"}</dd>
        <dt>發行價格／區間</dt>
        <dd>{company.price ?? "尚未公布"}</dd>
      </dl>
      {Date.now() - Date.parse(company.checkedAt) > 7 * 86400000 && <p role="status">此申報紀錄已超過七天未核對，最新定價與掛牌狀態請查看官方公告。</p>}
      <p>
        核對時間 {formatDateTime(company.checkedAt)}
        （台灣時間）。預計日期可能變動，申報不代表已核准或已掛牌。
      </p>
      <p>
        <a href={company.sourceUrl} target="_blank" rel="noopener noreferrer">
          查看官方申報／IPO 行事曆
        </a>
      </p>
      {company.issuerUrl && (
        <p>
          <a href={company.issuerUrl} target="_blank" rel="noopener noreferrer">
            公司官方公告
          </a>
        </p>
      )}
    </section>
  );
}
