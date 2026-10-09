-- 自選股設定頁改經 Edge Function user-settings 寫入（先驗證 LIFF ID Token，再用 service role）。
-- 移除 anon / authenticated 直接寫入 groups、stocks、index_configs 的 policy。
-- 讀取 policy 保留：每日報告（main.py）與 Render FastAPI 仍用 publishable key 讀取。
-- 必須在 my-stock-backend 的設定頁改用 user-settings 並確認可正常儲存之後才手動套用
-- （故意不放在 supabase/migrations，避免合併時被自動執行）。

drop policy if exists "Allow public insert and update" on public.stocks;
drop policy if exists "限制只有管理員ID可以修改大盤" on public.stocks;

drop policy if exists "groups_insert_policy" on public.groups;
drop policy if exists "groups_update_policy" on public.groups;
drop policy if exists "groups_delete_policy" on public.groups;

drop policy if exists "限制只能修改特定大盤代號" on public.index_configs;
