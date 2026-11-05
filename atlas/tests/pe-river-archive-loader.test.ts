import { beforeEach, expect, it, vi } from 'vitest';
import type { BarResult } from '../src/market-data/MarketDataProvider';
const provider = vi.hoisted(()=>({getBars:vi.fn(),getOlderBars:vi.fn()}));
vi.mock('../src/app/dataSources',()=>({marketProvider:()=>provider}));
import { loadWeeklyPrices } from '../src/ui/financials/PeRiverCard';
const current: BarResult = {bars:[{time:1767571200,open:100,high:101,low:99,close:100,volume:1000}],
  archive:{lastDate:'2026-01-05',status:'archive'},source:'archive',session:'regular',delayed:true,adjusted:true,priceBasis:'split-adjusted'};
beforeEach(()=>{vi.resetAllMocks();provider.getBars.mockResolvedValue(current);});
it('preserves the existing longer P/E history when archived weekly prices are available',async()=>{
  provider.getOlderBars.mockResolvedValue({...current,bars:[{...current.bars[0]!,time:1736121600},...current.bars]});
  const result=await loadWeeklyPrices('AAPL',new AbortController().signal);
  expect(result.status).toBe('ok');if(result.status==='ok')expect(result.bars).toHaveLength(2);
  expect(provider.getOlderBars).toHaveBeenCalledTimes(1);
});
it('a failed older-period supplement retains the available archived P/E prices',async()=>{
  provider.getOlderBars.mockRejectedValue(new Error('Yahoo 502'));
  const result=await loadWeeklyPrices('AAPL',new AbortController().signal);
  expect(result.status).toBe('ok');if(result.status==='ok')expect(result.bars).toHaveLength(1);
});
it('leaves the original non-archive P/E provider flow unchanged',async()=>{
  provider.getBars.mockResolvedValue({...current,archive:undefined});
  expect((await loadWeeklyPrices('AAPL',new AbortController().signal)).status).toBe('ok');
  expect(provider.getOlderBars).not.toHaveBeenCalled();
});
