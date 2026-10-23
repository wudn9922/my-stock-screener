/** Server-only account storage policy. A verified LINE identity is required by callers. */
export const DEFAULT_ADMIN_LINE_ID = "Ua0037dbd5037b64fc76804c60520ad76";
export const LINE_USER_ID_PATTERN = /^U[0-9a-f]{32}$/;
export const STORAGE_ACCESS_TABLE = "atlas_storage_access";
export const MAX_STORAGE_GRANTS = 2000;
export type StorageMode = "none" | "device" | "cloud";
export interface StorageAccess {
  userId: string;
  isAdmin: boolean;
  storageMode: StorageMode;
  revision: string;
  policyAvailable: boolean;
}
export interface StorageGrant {
  userId: string;
  label: string;
  storageMode: StorageMode;
  revision: string;
}

interface PolicyRow {
  line_user_id: string;
  storage_mode: StorageMode;
  label: string;
  updated_at: string;
}
interface DatabaseError { code?: string; message?: string }
interface PolicyResult {
  data: PolicyRow | PolicyRow[] | null;
  error: DatabaseError | null;
  count?: number | null;
}
interface PolicyQuery extends PromiseLike<PolicyResult> {
  select(columns: string, options?: { count?: "exact"; head?: boolean }): PolicyQuery;
  eq(column: string, value: unknown): PolicyQuery;
  order(column: string, options?: { ascending?: boolean }): PolicyQuery;
  limit(value: number): PolicyQuery;
  maybeSingle(): PolicyQuery;
  single(): PolicyQuery;
  insert(value: Record<string, unknown>): PolicyQuery;
  update(value: Record<string, unknown>): PolicyQuery;
}
/** The Supabase adapter stays here; consumers never accept a browser-supplied permission flag. */
export interface PolicyDatabase { from(table: string): unknown }
const table = (database: PolicyDatabase) => database.from(STORAGE_ACCESS_TABLE) as PolicyQuery;
const COLUMNS = "line_user_id,storage_mode,label,updated_at";

export class StorageAccessError extends Error {
  constructor(readonly status: number, message: string, readonly code: string) {
    super(message);
  }
}

export function adminLineId(value?: string | null): string {
  const userId = (value || DEFAULT_ADMIN_LINE_ID).trim();
  if (!LINE_USER_ID_PATTERN.test(userId))
    throw new StorageAccessError(503, "管理員帳號設定不正確，儲存權限暫時停用。", "ACCESS_UNAVAILABLE");
  return userId;
}
export function parseStorageUserId(value: unknown): string {
  if (typeof value !== "string" || !LINE_USER_ID_PATTERN.test(value))
    throw new StorageAccessError(400, "LINE 帳號 ID 格式不正確。", "INVALID_ACCOUNT");
  return value;
}
export function isStorageMode(value: unknown): value is StorageMode {
  return value === "none" || value === "device" || value === "cloud";
}
function missingTable(error: DatabaseError) {
  return error.code === "42P01" || error.code === "PGRST205";
}
function unavailable() {
  return new StorageAccessError(503, "無法確認儲存權限，目前僅能暫存，請稍後再試。", "ACCESS_UNAVAILABLE");
}
function migrationRequired() {
  return new StorageAccessError(503, "儲存授權資料表尚未部署，請先套用 account_storage_permissions.sql。", "ACCESS_SCHEMA_REQUIRED");
}
function grant(row: PolicyRow): StorageGrant {
  if (!LINE_USER_ID_PATTERN.test(row.line_user_id) || !isStorageMode(row.storage_mode)
    || typeof row.updated_at !== "string" || !Number.isFinite(Date.parse(row.updated_at))
    || typeof row.label !== "string" || row.label.length > 80) throw unavailable();
  return { userId: row.line_user_id, label: row.label, storageMode: row.storage_mode, revision: row.updated_at };
}
function defaultAccess(userId: string, adminId: string, policyAvailable: boolean): StorageAccess {
  const isAdmin = userId === adminId;
  return {
    userId, isAdmin, storageMode: isAdmin ? "cloud" : "none",
    revision: isAdmin ? "owner-default" : "none-default", policyAvailable,
  };
}

/** Read-only: an ungranted visitor never creates an account/policy/database row. */
export async function getStorageAccess(database: PolicyDatabase, userId: string, adminId: string): Promise<StorageAccess> {
  parseStorageUserId(userId);
  const { data, error } = await table(database).select(COLUMNS).eq("line_user_id", userId).maybeSingle();
  if (error) {
    if (missingTable(error)) return defaultAccess(userId, adminId, false);
    throw unavailable();
  }
  if (!data) return defaultAccess(userId, adminId, true);
  if (Array.isArray(data)) throw unavailable();
  const row = grant(data);
  if (row.userId !== userId) throw unavailable();
  return { userId, isAdmin: userId === adminId, storageMode: row.storageMode, revision: row.revision, policyAvailable: true };
}

/** Every private-data endpoint must call this before touching a personal data table. */
export async function requireCloudStorage(database: PolicyDatabase, userId: string, adminId: string): Promise<StorageAccess> {
  const access = await getStorageAccess(database, userId, adminId);
  if (access.storageMode !== "cloud")
    throw new StorageAccessError(403, "此帳號未獲准使用 Supabase 儲存；請使用目前核准的暫存或裝置模式。", "STORAGE_NOT_CLOUD");
  return access;
}

export function requireStorageAdmin(userId: string, adminId: string) {
  if (userId !== adminId)
    throw new StorageAccessError(403, "只有管理員可以查看或變更帳號儲存權限。", "ADMIN_REQUIRED");
}

export async function listStorageGrants(database: PolicyDatabase, userId: string, adminId: string): Promise<StorageGrant[]> {
  requireStorageAdmin(userId, adminId);
  const { data, error } = await table(database).select(COLUMNS).order("updated_at", { ascending: false }).limit(MAX_STORAGE_GRANTS + 1);
  if (error) throw missingTable(error) ? migrationRequired() : unavailable();
  if (!Array.isArray(data) || data.length > MAX_STORAGE_GRANTS) throw unavailable();
  const rows = data.map(grant);
  if (!rows.some((row) => row.userId === adminId))
    rows.unshift({ userId: adminId, label: "管理員", storageMode: "cloud", revision: "owner-default" });
  return rows;
}

export async function saveStorageGrant(database: PolicyDatabase, userId: string, adminId: string, body: Record<string, unknown>): Promise<StorageGrant> {
  requireStorageAdmin(userId, adminId);
  const target = parseStorageUserId(body.userId);
  if (!isStorageMode(body.storageMode))
    throw new StorageAccessError(400, "儲存模式必須是 none、device 或 cloud。", "INVALID_STORAGE_MODE");
  const label = body.label === undefined ? "" : typeof body.label === "string" ? body.label.trim() : null;
  if (label === null || label.length > 80 || /[\u0000-\u001f\u007f]/.test(label))
    throw new StorageAccessError(400, "帳號名稱最多 80 個字，不能含控制字元。", "INVALID_LABEL");
  const current = await getStorageAccess(database, target, adminId);
  if (!current.policyAvailable) throw migrationRequired();
  if (body.expectedRevision !== undefined && body.expectedRevision !== current.revision)
    throw new StorageAccessError(409, "此帳號授權已變更，請重新載入後再儲存。", "ACCESS_CONFLICT");
  const isDefault = current.revision === "owner-default" || current.revision === "none-default";
  const values = { line_user_id: target, storage_mode: body.storageMode, label, updated_by: userId };
  let result: PolicyResult;
  if (isDefault) {
    const { count, error } = await table(database).select("line_user_id", { count: "exact", head: true });
    if (error) throw missingTable(error) ? migrationRequired() : unavailable();
    if (!Number.isSafeInteger(count) || count! < 0) throw unavailable();
    if (count! >= MAX_STORAGE_GRANTS)
      throw new StorageAccessError(409, "儲存授權帳號數量已達上限。", "ACCESS_LIMIT");
    result = await table(database).insert(values).select(COLUMNS).single();
  } else {
    result = await table(database).update(values).eq("line_user_id", target).eq("updated_at", current.revision).select(COLUMNS).maybeSingle();
  }
  if (result.error) {
    if (result.error.code === "23505")
      throw new StorageAccessError(409, "此帳號授權已變更，請重新載入後再儲存。", "ACCESS_CONFLICT");
    throw missingTable(result.error) ? migrationRequired() : unavailable();
  }
  if (!result.data)
    throw new StorageAccessError(409, "此帳號授權已變更，請重新載入後再儲存。", "ACCESS_CONFLICT");
  if (Array.isArray(result.data)) throw unavailable();
  return grant(result.data);
}
