import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runInNewContext } from 'node:vm';
import { expect, it, vi } from 'vitest';
import { versionedServiceWorker } from '../src/app/buildPlugins';

async function builtWorker() {
  const out=await mkdtemp(join(tmpdir(),'atlas-public-chunks-'));
  try {
    await writeFile(join(out,'index.html'),'<head><script src="/assets/index-a.js"></script></head>');
    await writeFile(join(out,'startup.js'),'/* public startup fixture */');
    await writeFile(join(out,'sw.js'),await readFile(new URL('../public/sw.js',import.meta.url),'utf8'));
    const plugin=versionedServiceWorker();
    const invoke=(hook:unknown,...args:unknown[])=>(hook as (...values:unknown[])=>unknown)(...args);
    invoke(plugin.configResolved,{build:{outDir:out},base:'/'});
    invoke(plugin.generateBundle,{},Object.fromEntries(['assets/index-a.js','assets/ChartPage-b.js','assets/ChartPage-c.css','api/private.js','assets/../api/private.js','report/latest.json'].map(name=>[name,{}])));
    await invoke(plugin.closeBundle);
    return await readFile(join(out,'sw.js'),'utf8');
  } finally {await rm(out,{recursive:true,force:true});}
}
async function install(scope:string,source:string) {
  type InstallEvent={waitUntil:(value:Promise<unknown>)=>void};
  const handlers=new Map<string,(event:InstallEvent)=>void>();
  const put=vi.fn(async(_key:string,_response:Response)=>{}),open=vi.fn(async()=>({put})),skipWaiting=vi.fn(async()=>{});
  const fetcher=vi.fn(async()=>new Response('<head><script src="/assets/index-a.js"></script></head>'));
  runInNewContext(source,{URL,Request,Response,Map,Promise,fetch:fetcher,caches:{open},
    self:{registration:{scope},skipWaiting,addEventListener:(name:string,callback:(event:InstallEvent)=>void)=>handlers.set(name,callback)}});
  let completion=Promise.resolve<unknown>(null);
  handlers.get('install')!({waitUntil:value=>{completion=value;}});await completion;
  return {put,open,skipWaiting,fetcher};
}
it('legacy offline shell includes lazy public chart JS/CSS, without preloading reports or API paths',async()=>{
  const source=await builtWorker();const {put}=await install('https://example.test/',source);
  const cached=put.mock.calls.map(call=>call[0]);
  expect(cached).toContain('https://example.test/assets/ChartPage-b.js');
  expect(cached).toContain('https://example.test/assets/ChartPage-c.css');
  expect(cached.some(url=>url.includes('/api/')||url.includes('/report/'))).toBe(false);
  expect(source).not.toContain('const BUILD_ASSETS = [];');
});
it('the screener worker still skips storage and all precache fetches',async()=>{
  const source=await builtWorker();const {open,fetcher,skipWaiting}=await install('https://example.test/my-stock-screener/atlas/',source);
  expect(skipWaiting).toHaveBeenCalledTimes(1);expect(open).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
});
