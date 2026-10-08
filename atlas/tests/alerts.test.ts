import { describe,it,expect } from 'vitest';
import { alertSchema } from '../src/storage/schema';
import { evaluateAlerts,reconcileDrawingAlerts,referenceLevel } from '../src/alerts/AlertEngine';
import type { Bar } from '../src/market-data/MarketDataProvider';
import type { Drawing } from '../src/drawing/DrawingModel';
const bars=(values:number[]):Bar[]=>values.map((close,i)=>({time:1700000000+i*300,open:close,high:close+1,low:close-1,close,volume:100}));
const definition=()=>alertSchema.parse({id:'alert',symbol:'AAPL',timeframe:'5m',kind:'level',direction:'above',level:10,enabled:true});
const drawing:Drawing={id:'level',symbol:'AAPL',type:'horizontal',points:[{time:1700000000,logical:0,price:10,timeframe:'5m'}],locked:false,visible:true,scope:{timeframes:'all'},style:{color:'#123456',lineWidth:2}};
describe('active-app alerts',()=>{
 it('initializes latest closed bar without retrospective spam; triggers once and re-arms',()=>{
   const initialized=evaluateAlerts([definition()],bars([9,11,9]),[],true);
   expect(initialized.events).toEqual([]);
   const fired=evaluateAlerts(initialized.definitions,bars([9,11,9,12,13]),[]);
   expect(fired.events).toHaveLength(1);
   expect(evaluateAlerts(fired.definitions,bars([9,11,9,12,13]),[]).events).toEqual([]);
   const rearmed=evaluateAlerts(fired.definitions,bars([9,11,9,12,13,8,12]),[]);
   expect(rearmed.events).toHaveLength(1);
   expect(rearmed.definitions[0].lastTriggered).toBe(bars([9,11,9,12,13,8,12]).at(-1)!.time);
 });
 it('does not evaluate disabled definitions and treats equality as neutral',()=>{
   const a=evaluateAlerts([definition()],bars([9]),[],true).definitions;
   const run=evaluateAlerts(a,bars([9,10,10,11]),[]);
   expect(run.events).toHaveLength(1);
   expect(evaluateAlerts([{...a[0],enabled:false}],bars([9,11]),[]).events).toEqual([]);
 });
 it('evaluates SMA/EMA using warmed-up closed bar values',()=>{
   for(const maType of ['SMA','EMA'] as const){
    const alert=alertSchema.parse({...definition(),kind:'ma',maType,period:2,direction:'cross'});
    const start=evaluateAlerts([alert],bars([10,9]),[],true);
    expect(evaluateAlerts(start.definitions,bars([10,9,12]),[]).events).toHaveLength(1);
   }
 });
 it('drawing movement resets baseline, deletion invalidates safely and cannot orphan',()=>{
   const a=alertSchema.parse({...definition(),kind:'drawing',drawingId:'level'});
   const start=evaluateAlerts([a],bars([9]),[drawing],true);
   const moved={...drawing,points:[{...drawing.points[0],price:5}]};
   const reconciled=reconcileDrawingAlerts(start.definitions,'AAPL',[moved]);
   expect(reconciled[0].lastEvaluated).toBeNull();
   expect(evaluateAlerts(reconciled,bars([9,11]),[moved]).events).toEqual([]);
   const deleted=reconcileDrawingAlerts(reconciled,'AAPL',[]);
   expect(deleted[0].enabled).toBe(false);
   expect(deleted[0].invalidReason).toContain('deleted');
   expect(reconcileDrawingAlerts(start.definitions,'NVDA',[])).toEqual(start.definitions);
 });
 it('drawing alerts respect owner timeframe while hidden drawings remain valid references',()=>{
   const daily={...drawing,visible:false,scope:{timeframes:['1D'] as '1D'[]}};
   const dailyAlert=alertSchema.parse({...definition(),timeframe:'1D',kind:'drawing',drawingId:'level'});
   const weeklyAlert=alertSchema.parse({...definition(),timeframe:'1W',kind:'drawing',drawingId:'level'});
   expect(referenceLevel(dailyAlert,[daily])).toBe(10);
   expect(referenceLevel(weeklyAlert,[daily])).toBeNull();
   const ready=evaluateAlerts([dailyAlert],bars([9]),[daily],true);
   expect(evaluateAlerts(ready.definitions,bars([9,11]),[daily]).events).toHaveLength(1);
   const mismatch=evaluateAlerts([weeklyAlert],bars([9]),[daily]);
   expect(mismatch.events).toEqual([]);
   expect(mismatch.definitions[0]).toMatchObject({enabled:false,invalidReason:'Referenced drawing is scoped to another timeframe'});
   expect(reconcileDrawingAlerts([weeklyAlert],'AAPL',[daily])[0]).toMatchObject({enabled:false,invalidReason:'Referenced drawing is scoped to another timeframe'});
 });
});
