import { z } from "zod";
import { reportUrl } from "../report/loadReport";
import { INITIAL_IPOS } from "./initial";
const sourceUrl = z
  .string()
  .url()
  .refine((value) => {
    const u = new URL(value);
    return (
      u.protocol === "https:" &&
      ["www.sec.gov", "www.nasdaq.com", "www.anthropic.com"].includes(
        u.hostname,
      )
    );
  });
export const ipoSchema = z.object({
  id: z.string().min(1).max(100),
  name: z.string().min(1).max(250),
  symbol: z.string().max(20),
  aliases: z.array(z.string()).max(20),
  status: z.string().max(100),
  exchange: z.string().max(100),
  filedDate: z.string().nullable(),
  listingDate: z.string().nullable(),
  expectedPricingDate: z.string().nullable().optional(),
  pricingDate: z.string().nullable().optional(),
  price: z.string().max(100).nullable(),
  checkedAt: z.string().datetime({ offset: true }),
  sourceUrl,
  issuerUrl: sourceUrl.optional(),
});
export type IpoCompany = z.infer<typeof ipoSchema>;
const schema = z.object({
  version: z.literal(1),
  generatedAt: z.string(),
  companies: z.array(ipoSchema).max(1000),
  reason: z.string().nullable().optional(),
});
export async function loadIpos(
  signal?: AbortSignal,
): Promise<{ companies: IpoCompany[]; reason?: string }> {
  let failure = "無法連線至 IPO 更新檔，請檢查網路";
  try {
    const response = await fetch(new URL("ipo-watch.json", reportUrl()), {
      cache: "no-cache",
      signal,
    });
    if (!response.ok) {
      failure = `IPO 更新檔載入失敗（HTTP ${response.status}）`;
      throw new Error('public-source-http');
    }
    failure = "IPO 更新檔的 JSON 或資料格式無效";
    const value = schema.parse(await response.json());
    return {
      companies: value.companies,
      ...(value.reason ? { reason: value.reason } : {}),
    };
  } catch {
    return {
      companies: INITIAL_IPOS,
      reason:
        `${failure}；以下為已核對的申報紀錄，請以官方最新公告為準。`,
    };
  }
}
export function matchIpos(
  companies: readonly IpoCompany[],
  query: string,
): IpoCompany[] {
  const q = query.trim().toLocaleLowerCase();
  return companies
    .filter(
      (item) =>
        !q ||
        [item.name, item.symbol, ...item.aliases].some((value) =>
          value.toLocaleLowerCase().includes(q),
        ),
    )
    .slice(0, 8);
}
