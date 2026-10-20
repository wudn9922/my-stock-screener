import { createClient } from "@supabase/supabase-js";
import { validateWatchlist, watchlistId, watchlistVersion } from "./watchlists.ts";

/**
 * 自選股設定頁（my-stock-backend/index.html）的讀寫入口。
 *
 * 瀏覽器只能用 publishable key，無法證明自己是哪個 LINE 使用者，所以 groups / stocks /
 * index_configs 的寫入改由這裡處理：先向 LINE 驗證 LIFF ID Token，再用 service role
 * 只操作該使用者自己的資料列。資料表本身不再開放 anon 寫入。
 *
 * POST JSON { idToken, action, ... } → { ok: true, data } 或 { ok: false, error }
 *   groups.list
 *   groups.create { name }
 *   groups.rename { id, name }
 *   groups.delete { id }
 *   stocks.list
 *   stocks.save   { id?, ticker, groupId, mas: number[] }  有 id 為修改，否則新增（同代號同分組則覆蓋均線）
 *                 groupId：tw_g1…us_g4 或 custom_<groups.id>
 *   stocks.delete { id }
 *   admin.index.list                                      僅管理員（回傳目前已設定的大盤均線）
 *   admin.index.save { ticker, name, mas: number[] }      僅管理員
 */

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": [
    "authorization",
    "x-client-info",
    "apikey",
    "content-type",
  ].join(", "),
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// 與原本 RLS policy 相同的管理員與可修改的大盤代號
const DEFAULT_ADMIN_LINE_ID = "Ua0037dbd5037b64fc76804c60520ad76";
const ADMIN_INDEX_TICKERS = new Set([
  "^TWII",
  "^TWOII",
  "^GSPC",
  "^DJI",
  "^IXIC",
  "^RUT",
  "^SOX",
  "^FCHI",
  "^FTSE",
  "^GDAXI",
  "^N225",
  "^KS11",
]);
const FIXED_GROUP_IDS = new Set([
  "tw_g1",
  "tw_g2",
  "us_g1",
  "us_g2",
  "us_g3",
  "us_g4",
]);
const ADMIN_GROUP_ID = "admin_index";
// A persistent preference, excluded from report/monitor membership by both consumers.
const FIXED_STOCK_GROUP_ID = "ungrouped";
const TICKER_PATTERN = /^[A-Z0-9.^_-]{1,30}$/;
const GROUP_NAME_MAX = 30;
const MAX_GROUPS_PER_USER = 50;
const MAX_STOCKS_PER_USER = 500;

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function jsonResponse(
  body: Record<string, unknown>,
  status = 200,
) {
  return new Response(
    JSON.stringify(body),
    {
      status,
      headers: {
        ...corsHeaders,
        "Content-Type": "application/json; charset=utf-8",
      },
    },
  );
}

async function verifyLineIdToken(idToken: string) {
  const channelId = Deno.env.get(
    "LINE_LOGIN_CHANNEL_ID",
  );

  if (!channelId) {
    throw new Error(
      "LINE_LOGIN_CHANNEL_ID is not configured",
    );
  }

  const formData = new URLSearchParams();

  formData.set("id_token", idToken);
  formData.set("client_id", channelId);

  const response = await fetch(
    "https://api.line.me/oauth2/v2.1/verify",
    {
      method: "POST",
      headers: {
        "Content-Type":
          "application/x-www-form-urlencoded",
      },
      body: formData.toString(),
    },
  );

  const result = await response.json();

  if (!response.ok) {
    console.error(
      "LINE ID Token verification failed",
      result,
    );

    throw new HttpError(401, "Invalid LINE ID Token");
  }

  const lineUserId = String(
    result.sub ?? "",
  ).trim();

  if (!lineUserId.startsWith("U")) {
    throw new HttpError(401, "Invalid LINE User ID");
  }

  return lineUserId;
}

function parseId(value: unknown) {
  const id = Number(value);

  if (!Number.isSafeInteger(id) || id <= 0) {
    throw new HttpError(400, "Invalid id");
  }

  return id;
}

function parseGroupName(value: unknown) {
  const name = String(value ?? "").trim();

  if (!name || name.length > GROUP_NAME_MAX) {
    throw new HttpError(
      400,
      `分組名稱需為 1～${GROUP_NAME_MAX} 個字`,
    );
  }

  return name;
}

function parseTicker(value: unknown) {
  const ticker = String(value ?? "").trim().toUpperCase();

  if (!TICKER_PATTERN.test(ticker)) {
    throw new HttpError(400, "股票代號格式不正確");
  }

  return ticker;
}

/** 與設定頁 parseMovingAverages 相同：1～4 個、不重複、1～1000 的整數。 */
function parseMovingAverages(value: unknown) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4) {
    throw new HttpError(400, "均線參數需為 1～4 組");
  }

  const mas = value.map(Number);

  if (
    mas.some((number) =>
      !Number.isInteger(number) || number <= 0 || number > 1000
    )
  ) {
    throw new HttpError(400, "均線必須是 1～1000 的正整數");
  }

  if (new Set(mas).size !== mas.length) {
    throw new HttpError(400, "均線參數不可重複");
  }

  return {
    ma1: mas[0] ?? 0,
    ma2: mas[1] ?? 0,
    ma3: mas[2] ?? 0,
    ma4: mas[3] ?? 0,
  };
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response("ok", {
      headers: corsHeaders,
    });
  }

  if (request.method !== "POST") {
    return jsonResponse(
      {
        ok: false,
        error: "Method not allowed",
      },
      405,
    );
  }

  try {
    let body: Record<string, unknown>;

    try {
      body = await request.json();
    } catch {
      throw new HttpError(400, "Invalid JSON");
    }

    const action = String(
      body.action ?? "",
    ).trim();

    const idToken = String(
      body.idToken ?? "",
    ).trim();

    if (!idToken) {
      throw new HttpError(401, "Missing LIFF ID Token");
    }

    const lineUserId = await verifyLineIdToken(
      idToken,
    );

    const supabaseUrl = Deno.env.get(
      "SUPABASE_URL",
    );

    const serviceRoleKey = Deno.env.get(
      "SUPABASE_SERVICE_ROLE_KEY",
    );

    if (!supabaseUrl || !serviceRoleKey) {
      throw new Error(
        "Supabase server configuration is missing",
      );
    }

    const supabase = createClient(
      supabaseUrl,
      serviceRoleKey,
      {
        auth: {
          persistSession: false,
          autoRefreshToken: false,
        },
      },
    );

    // 固定分組，或使用者自己的自訂分組（stocks.group_id 存成 custom_<groups.id>）
    const ownsGroup = async (groupId: string) => {
      if (FIXED_GROUP_IDS.has(groupId) || groupId === FIXED_STOCK_GROUP_ID) {
        return true;
      }

      const match = /^custom_(\d{1,18})$/.exec(groupId);

      if (!match) {
        return false;
      }

      const { data, error } = await supabase
        .from("groups")
        .select("id")
        .eq("id", Number(match[1]))
        .eq("line_user_id", lineUserId)
        .maybeSingle();

      if (error) {
        throw error;
      }

      return !!data;
    };

    if (action.startsWith("watchlists.")) {
      const dbError = (error: { code?: string; message?: string }) => {
        if (error.code === "42P01" || error.code === "PGRST205") throw new HttpError(503, "雲端清單資料表尚未部署，請先套用 atlas_watchlists.sql");
        if (error.code === "23505") throw new HttpError(409, "雲端已有這組清單，請先載入雲端版本再同步");
        throw error;
      };
      if (action === "watchlists.list") {
        const { data, error } = await supabase.from("atlas_watchlists")
          .select("id,definition,updated_at").eq("line_user_id", lineUserId).order("updated_at", { ascending: false });
        if (error) dbError(error);
        return jsonResponse({ ok: true, data: data ?? [] });
      }
      if (action === "watchlists.save") {
        let definition: ReturnType<typeof validateWatchlist>;
        let expected: string | null;
        try { definition = validateWatchlist(body.definition); expected = body.expectedUpdatedAt == null ? null : watchlistVersion(body.expectedUpdatedAt); }
        catch (error) { throw new HttpError(400, error instanceof Error ? error.message : "清單格式不正確"); }
        if (expected === null) {
          const { count, error: countError } = await supabase.from("atlas_watchlists").select("id", { count: "exact", head: true }).eq("line_user_id", lineUserId);
          if (countError) dbError(countError);
          if ((count ?? 0) >= 30) throw new HttpError(400, "雲端清單最多 30 組");
          const { data, error } = await supabase.from("atlas_watchlists").insert({ id: definition.id, line_user_id: lineUserId, definition }).select("id,definition,updated_at").single();
          if (error) dbError(error);
          return jsonResponse({ ok: true, data });
        }
        const { data, error } = await supabase.from("atlas_watchlists").update({ definition })
          .eq("line_user_id", lineUserId).eq("id", definition.id).eq("updated_at", expected).select("id,definition,updated_at").maybeSingle();
        if (error) dbError(error);
        if (!data) throw new HttpError(409, "雲端清單已在其他裝置更新；本機資料未覆寫，請先載入雲端版本");
        return jsonResponse({ ok: true, data });
      }
      if (action === "watchlists.delete") {
        let id: string, expected: string;
        try { id = watchlistId(body.id); expected = watchlistVersion(body.expectedUpdatedAt); }
        catch (error) { throw new HttpError(400, error instanceof Error ? error.message : "版本不正確"); }
        const { data, error } = await supabase.from("atlas_watchlists").delete().eq("line_user_id", lineUserId)
          .eq("id", id).eq("updated_at", expected).select("id");
        if (error) dbError(error);
        if (!data?.length) {
          const { data: current, error: readError } = await supabase.from("atlas_watchlists").select("id")
            .eq("line_user_id", lineUserId).eq("id", id).maybeSingle();
          if (readError) dbError(readError);
          if (current) throw new HttpError(409, "雲端版本已變更，請重新載入再刪除");
          // A list already deleted on another device is successfully absent.
        }
        return jsonResponse({ ok: true, data });
      }
      throw new HttpError(400, "Unknown watchlist action");
    }

    if (action === "groups.list") {
      const { data, error } = await supabase
        .from("groups")
        .select("id, name, line_user_id, ma1, ma2, ma3, ma4")
        .eq("line_user_id", lineUserId)
        .order("created_at", { ascending: true });

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "groups.create") {
      const name = parseGroupName(body.name);

      const { count, error: countError } = await supabase
        .from("groups")
        .select("id", { count: "exact", head: true })
        .eq("line_user_id", lineUserId);

      if (countError) {
        throw countError;
      }

      if ((count ?? 0) >= MAX_GROUPS_PER_USER) {
        throw new HttpError(400, "分組數量已達上限");
      }

      const { data, error } = await supabase
        .from("groups")
        .insert({
          name,
          line_user_id: lineUserId,
          ma1: 5,
          ma2: 10,
          ma3: 20,
          ma4: 60,
        })
        .select("id, name, line_user_id, ma1, ma2, ma3, ma4")
        .single();

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data });
    }

    if (action === "groups.rename") {
      const id = parseId(body.id);
      const name = parseGroupName(body.name);

      const { data, error } = await supabase
        .from("groups")
        .update({ name })
        .eq("id", id)
        .eq("line_user_id", lineUserId)
        .select("id, name");

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "groups.delete") {
      const id = parseId(body.id);
      const { count, error: countError } = await supabase
        .from("stocks")
        .select("id", { count: "exact", head: true })
        .eq("line_user_id", lineUserId)
        .eq("group_id", `custom_${id}`);

      if (countError) {
        throw countError;
      }

      if ((count ?? 0) > 0) {
        throw new HttpError(409, "分組內仍有股票，請先移除");
      }

      const { data, error } = await supabase
        .from("groups")
        .delete()
        .eq("id", id)
        .eq("line_user_id", lineUserId)
        .select("id");

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "stocks.list") {
      const { data, error } = await supabase
        .from("stocks")
        .select("id, ticker, group_id, ma1, ma2, ma3, ma4")
        .eq("line_user_id", lineUserId)
        .neq("group_id", ADMIN_GROUP_ID)
        .order("ticker", { ascending: true });

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "stocks.save") {
      const ticker = parseTicker(body.ticker);
      const groupId = String(body.groupId ?? "").trim();
      const mas = parseMovingAverages(body.mas);
      // Older monitor builds treat unknown groups as active. Enable only after both
      // consumers have been deployed with explicit dormant-row exclusion.
      if (groupId === FIXED_STOCK_GROUP_ID && Deno.env.get("ATLAS_FIXED_PARAMETERS_ENABLED") !== "1") {
        throw new HttpError(503, "個股固定參數尚未啟用，需先完成服務更新；目前未儲存");
      }

      if (!groupId || groupId === ADMIN_GROUP_ID || !(await ownsGroup(groupId))) {
        throw new HttpError(400, "分組不存在或沒有權限");
      }

      const saveData = {
        line_user_id: lineUserId,
        ticker,
        group_id: groupId,
        ...mas,
      };

      if (body.id !== undefined && body.id !== null && body.id !== "") {
        const id = parseId(body.id);

        const { data, error } = await supabase
          .from("stocks")
          .update(saveData)
          .eq("id", id)
          .eq("line_user_id", lineUserId)
          .neq("group_id", ADMIN_GROUP_ID)
          .select("id");

        if (error) {
          throw error;
        }
        if (!data?.length) throw new HttpError(404, "這筆股票參數不存在或沒有權限");
        return jsonResponse({ ok: true, data: data ?? [] });
      }

      // An existing preference remains editable even when the stock limit is reached.
      const { data: existing, error: existingError } = await supabase.from("stocks").select("id")
        .eq("line_user_id", lineUserId).eq("ticker", ticker).eq("group_id", groupId).maybeSingle();
      if (existingError) throw existingError;
      const { count, error: countError } = await supabase
        .from("stocks")
        .select("id", { count: "exact", head: true })
        .eq("line_user_id", lineUserId);

      if (countError) {
        throw countError;
      }

      if (!existing && (count ?? 0) >= MAX_STOCKS_PER_USER) {
        throw new HttpError(400, "自選股數量已達上限");
      }

      const { data, error } = await supabase
        .from("stocks")
        .upsert(saveData, {
          onConflict: "line_user_id,ticker,group_id",
        })
        .select("id");

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "stocks.delete") {
      const id = parseId(body.id);

      const { data, error } = await supabase
        .from("stocks")
        .delete()
        .eq("id", id)
        .eq("line_user_id", lineUserId)
        .neq("group_id", ADMIN_GROUP_ID)
        .select("id");

      if (error) {
        throw error;
      }
      if (!data?.length) throw new HttpError(404, "這筆股票參數不存在或已刪除");
      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "admin.index.list") {
      const adminId = (
        Deno.env.get("ADMIN_LINE_USER_ID") || DEFAULT_ADMIN_LINE_ID
      ).trim();

      if (lineUserId !== adminId) {
        throw new HttpError(403, "僅管理員可查看大盤參數");
      }

      const { data, error } = await supabase
        .from("index_configs")
        .select("ticker,name,ma1,ma2,ma3,ma4");

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, data: data ?? [] });
    }

    if (action === "admin.index.save") {
      const adminId = (
        Deno.env.get("ADMIN_LINE_USER_ID") || DEFAULT_ADMIN_LINE_ID
      ).trim();

      if (lineUserId !== adminId) {
        throw new HttpError(403, "僅管理員可修改大盤參數");
      }

      const ticker = String(body.ticker ?? "").trim().toUpperCase();

      if (!ADMIN_INDEX_TICKERS.has(ticker)) {
        throw new HttpError(400, "不支援的大盤代號");
      }

      const name = String(body.name ?? "").trim();

      if (!name || name.length > 50) {
        throw new HttpError(400, "大盤名稱格式不正確");
      }

      const mas = parseMovingAverages(body.mas);

      const { error } = await supabase
        .from("index_configs")
        .upsert(
          { ticker, name, ...mas },
          { onConflict: "ticker" },
        );

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true });
    }

    throw new HttpError(400, "Invalid action");
  } catch (error) {
    if (error instanceof HttpError) {
      return jsonResponse(
        { ok: false, error: error.message },
        error.status,
      );
    }

    console.error(error);

    // 資料庫錯誤只回傳通用訊息，細節留在函式記錄
    const message = error instanceof Error ? error.message : "";

    if (
      error && typeof error === "object" && "code" in error &&
      (error as { code?: string }).code === "23505"
    ) {
      return jsonResponse(
        { ok: false, error: "這檔股票已經在你的自選清單中" },
        409,
      );
    }

    return jsonResponse(
      {
        ok: false,
        error: message === "LINE_LOGIN_CHANNEL_ID is not configured"
          ? message
          : "Server error",
      },
      500,
    );
  }
});
