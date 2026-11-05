import { expect, it, vi } from 'vitest';
import { subscribeWatchlistQuotes } from '../src/market-data/watchlistQuotes';
import type { LiveDaily, LiveListener } from '../src/market-data/LiveQuotes';
import type { BarResult, Quote } from '../src/market-data/MarketDataProvider';
const data: BarResult = {bars:[{time:1767312000,open:100,high:101,low:99,close:100,volume:1000}],source:'archive',session:'regular',delayed:true,adjusted:true};
it('C4 subscribes only unique visible TW/US symbols and cleans up all subscriptions',async()=>{
  const callbacks=new Map<string,LiveListener>(),stops=[vi.fn(),vi.fn()];
  const feed={subscribe:vi.fn((symbol:string,callback:LiveListener)=>{callbacks.set(symbol,callback);return stops[callbacks.size-1]!;})};
  const history=vi.fn(async()=>data),receive=vi.fn();
  const stop=subscribeWatchlistQuotes(['2330.TW','AAPL','AAPL'],feed,history,receive);
  await Promise.resolve();
  expect(feed.subscribe.mock.calls.map(call=>call[0])).toEqual(['2330.TW','AAPL']);
  expect(history).toHaveBeenCalledTimes(2);expect(receive).toHaveBeenCalledTimes(2);
  stop();expect(stops.every(fn=>fn.mock.calls.length===1)).toBe(true);
  callbacks.get('AAPL')!({symbol:'AAPL',bars:data.bars,retrievedAt:1,quote:{symbol:'AAPL',price:200} as Quote});
  expect(receive).toHaveBeenCalledTimes(2);
});
it('C4 never lets delayed archive results overwrite a live tick, and ignores history after leaving',async()=>{
  let release!:(value:BarResult)=>void,callback!:LiveListener;
  const history=()=>new Promise<BarResult>(resolve=>{release=resolve;});
  const receive=vi.fn(),quote={symbol:'AAPL',price:105} as Quote;
  const feed={subscribe:(_symbol:string,listener:LiveListener)=>{callback=listener;return vi.fn();}};
  const stop=subscribeWatchlistQuotes(['AAPL'],feed,history,receive);
  callback({symbol:'AAPL',bars:data.bars,retrievedAt:2,quote} satisfies LiveDaily);
  release(data);await Promise.resolve();expect(receive).toHaveBeenCalledExactlyOnceWith('AAPL',quote);
  stop();
  const stopSecond=subscribeWatchlistQuotes(['AAPL'],feed,history,receive);stopSecond();release(data);
  await Promise.resolve();expect(receive).toHaveBeenCalledTimes(1);
});
