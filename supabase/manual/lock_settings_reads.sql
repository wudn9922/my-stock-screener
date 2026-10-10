-- 鎖定匿名讀取（P3）。groups、stocks、index_configs 含 line_user_id，不應讓匿名金鑰讀取。
--
-- 套用前必須全部完成，否則日報會讀不到自選股與大盤設定：
--   1. GitHub repo secrets 已新增 SUPABASE_SERVICE_ROLE_KEY
--      （值 = Supabase → Project Settings → API 的 service_role / secret key）。
--   2. 已確認 schedule.yml 用新 secret 跑過一次日報，log 沒有出現
--      「未設定 SUPABASE_SERVICE_ROLE_KEY，改用 anon key」。
--   3. 若 Render（my-stock-backend 的 FastAPI）仍要使用，Render 環境變數也要改成 service role key；
--      目前未使用，鎖定後它的讀寫都會失敗。
--
-- 之後所有讀寫都走 service role：日報（main.py）、Cloud Run 突破監控、Edge Functions。
-- 執行前需先執行 cleanup_policies_and_drawings.sql，或下列 drop 會一併處理重複的 policy。
-- 回復方式：見檔案最下方。

begin;

drop policy if exists "allow public read groups" on public.groups;
drop policy if exists "groups_select_policy" on public.groups;

drop policy if exists "Allow public read access" on public.index_configs;
drop policy if exists "允許所有人讀取大盤資料" on public.index_configs;

drop policy if exists "Allow public read access" on public.stocks;
drop policy if exists "允許所有人讀取股票與大盤資料" on public.stocks;

commit;

-- 驗證：三張表應沒有任何 policy（RLS 已啟用，匿名與登入使用者都讀不到，service role 不受 RLS 限制）
select tablename, policyname, cmd from pg_policies where schemaname = 'public' order by 1, 2;
select relname, relrowsecurity from pg_class
 where relnamespace = 'public'::regnamespace and relname in ('groups', 'stocks', 'index_configs');

-- 回復（若日報讀不到設定，先執行這段再排查）：
-- create policy "allow public read groups" on public.groups for select to anon, authenticated using (true);
-- create policy "Allow public read access" on public.index_configs for select using (true);
-- create policy "Allow public read access" on public.stocks for select using (true);
