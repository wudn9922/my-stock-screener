-- 清理（可隨時在 Supabase Dashboard → SQL editor 手動執行；故意不放在 supabase/migrations）
--
-- 1) 移除重複的 SELECT policy：groups、index_configs、stocks 各保留一條，行為不變。
-- 2) 刪除 chart_drawings 中舊版（timeframe = '1d'）的 4 筆畫線。
--    使用者已決定捨棄、不轉換；Atlas 新版畫線（timeframe = 'atlas'）不受影響。

begin;

drop policy if exists "groups_select_policy" on public.groups;
drop policy if exists "允許所有人讀取大盤資料" on public.index_configs;
drop policy if exists "允許所有人讀取股票與大盤資料" on public.stocks;

delete from public.chart_drawings where timeframe = '1d';

commit;

-- 驗證：應各剩一條 SELECT policy；chart_drawings 只剩 'atlas'
select tablename, policyname, cmd from pg_policies
 where schemaname = 'public' order by tablename, policyname;
select timeframe, count(*) from public.chart_drawings group by 1;
