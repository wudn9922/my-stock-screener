import { createClient } from "@supabase/supabase-js";

/**
 * 圖表畫線的雲端儲存（LINE 使用者各自一份，先向 LINE 驗證 LIFF ID Token，再用 service role 存取）。
 *
 * Atlas 網站（atlas/src/cloud/）：
 *   atlas.list                              → { ok, records: [{ symbol, drawings, updatedAt }] }
 *   atlas.save { symbol, drawings, updatedAt } → { ok: true, updatedAt }
 *                                             或 { ok: false, conflict: {...} }（雲端已有較新的編輯）
 *   每檔股票一列（timeframe = "atlas"），drawings 是 Atlas 所有週期的畫線；以編輯時間較新者為準。
 * 舊版報告（已停用）：load / save，timeframe 1d / 1w 的水平線段。
 */

const ATLAS_TIMEFRAME = "atlas";
const ATLAS_SYMBOL_PATTERN = /^[A-Z0-9.^=_-]{1,30}$/;
const ATLAS_MAX_DRAWINGS = 300;
const ATLAS_MAX_BYTES = 300000;
const ATLAS_MAX_RECORDS = 2000;

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

    throw new Error("Invalid LINE ID Token");
  }

  const lineUserId = String(
    result.sub ?? "",
  ).trim();

  if (!lineUserId.startsWith("U")) {
    throw new Error("Invalid LINE User ID");
  }

  return lineUserId;
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
    const body = await request.json();

    const action = String(
      body.action ?? "",
    ).trim();

    const idToken = String(
      body.idToken ?? "",
    ).trim();

    const ticker = String(
      body.ticker ?? "",
    ).trim().toUpperCase();

    const timeframe = String(
      body.timeframe ?? "",
    ).trim();

    const marketKey = String(
      body.marketKey ?? "",
    ).trim();

    if (!idToken) {
      return jsonResponse(
        {
          ok: false,
          error: "Missing LIFF ID Token",
        },
        401,
      );
    }

    const legacyAction = action === "load" || action === "save";

    if (
      legacyAction
      && (!ticker || !/^[A-Z0-9.^_-]{1,30}$/.test(ticker))
    ) {
      return jsonResponse(
        {
          ok: false,
          error: "Invalid ticker",
        },
        400,
      );
    }

    if (legacyAction && !["1d", "1w"].includes(timeframe)) {
      return jsonResponse(
        {
          ok: false,
          error: "Invalid timeframe",
        },
        400,
      );
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

    if (action === "atlas.list") {
      const { data, error } = await supabase
        .from("chart_drawings")
        .select("ticker, drawings, updated_at")
        .eq("line_user_id", lineUserId)
        .eq("timeframe", ATLAS_TIMEFRAME)
        .limit(ATLAS_MAX_RECORDS);

      if (error) {
        throw error;
      }

      return jsonResponse({
        ok: true,
        records: (data ?? []).map((row) => ({
          symbol: row.ticker,
          drawings: row.drawings ?? [],
          updatedAt: Date.parse(row.updated_at),
        })),
      });
    }

    if (action === "atlas.save") {
      const symbol = String(body.symbol ?? "").trim();
      const drawings = body.drawings;
      const requested = Number(body.updatedAt);

      if (!ATLAS_SYMBOL_PATTERN.test(symbol)) {
        return jsonResponse({ ok: false, error: "Invalid symbol" }, 400);
      }

      if (
        !Array.isArray(drawings)
        || drawings.length > ATLAS_MAX_DRAWINGS
        || drawings.some((drawing) =>
          !drawing || typeof drawing !== "object" || Array.isArray(drawing)
          || typeof (drawing as { id?: unknown }).id !== "string"
          || (drawing as { symbol?: unknown }).symbol !== symbol
        )
      ) {
        return jsonResponse({ ok: false, error: "Invalid drawings" }, 400);
      }

      if (JSON.stringify(drawings).length > ATLAS_MAX_BYTES) {
        return jsonResponse({ ok: false, error: "Drawing data is too large" }, 400);
      }

      if (!Number.isFinite(requested) || requested <= 0) {
        return jsonResponse({ ok: false, error: "Invalid updatedAt" }, 400);
      }

      // 用戶端時鐘超前時不能讓未來時間永遠勝出
      const updatedAt = Math.min(requested, Date.now());

      const { data: existing, error: readError } = await supabase
        .from("chart_drawings")
        .select("drawings, updated_at")
        .eq("line_user_id", lineUserId)
        .eq("ticker", symbol)
        .eq("timeframe", ATLAS_TIMEFRAME)
        .maybeSingle();

      if (readError) {
        throw readError;
      }

      if (existing && Date.parse(existing.updated_at) > updatedAt) {
        return jsonResponse({
          ok: false,
          conflict: {
            symbol,
            drawings: existing.drawings ?? [],
            updatedAt: Date.parse(existing.updated_at),
          },
        });
      }

      const { error } = await supabase
        .from("chart_drawings")
        .upsert(
          {
            line_user_id: lineUserId,
            ticker: symbol,
            timeframe: ATLAS_TIMEFRAME,
            market_key: null,
            drawings,
            updated_at: new Date(updatedAt).toISOString(),
          },
          {
            onConflict: "line_user_id,ticker,timeframe",
          },
        );

      if (error) {
        throw error;
      }

      return jsonResponse({ ok: true, updatedAt });
    }

    if (action === "load") {
      const { data, error } = await supabase
        .from("chart_drawings")
        .select(
          "drawings, updated_at, market_key",
        )
        .eq("line_user_id", lineUserId)
        .eq("ticker", ticker)
        .eq("timeframe", timeframe)
        .maybeSingle();

      if (error) {
        throw error;
      }

      return jsonResponse({
        ok: true,
        drawings: data?.drawings ?? [],
        updatedAt: data?.updated_at ?? null,
        marketKey: data?.market_key ?? null,
      });
    }

    if (action === "save") {
      const drawings = body.drawings;

      if (!Array.isArray(drawings)) {
        return jsonResponse(
          {
            ok: false,
            error: "drawings must be an array",
          },
          400,
        );
      }

      if (drawings.length > 200) {
        return jsonResponse(
          {
            ok: false,
            error: "Too many drawings",
          },
          400,
        );
      }

      const serializedDrawings =
        JSON.stringify(drawings);

      if (serializedDrawings.length > 200000) {
        return jsonResponse(
          {
            ok: false,
            error: "Drawing data is too large",
          },
          400,
        );
      }

      const updatedAt = new Date().toISOString();

      const { error } = await supabase
        .from("chart_drawings")
        .upsert(
          {
            line_user_id: lineUserId,
            ticker,
            timeframe,
            market_key: marketKey || null,
            drawings,
            updated_at: updatedAt,
          },
          {
            onConflict:
              "line_user_id,ticker,timeframe",
          },
        );

      if (error) {
        throw error;
      }

      return jsonResponse({
        ok: true,
        updatedAt,
      });
    }

    return jsonResponse(
      {
        ok: false,
        error: "Invalid action",
      },
      400,
    );
  } catch (error) {
    console.error(error);

    const message = error instanceof Error ? error.message : "";

    // LINE token 無效或過期回 401，前端據此提示重新開啟；其他錯誤不回傳內部細節
    if (message.startsWith("Invalid LINE")) {
      return jsonResponse({ ok: false, error: message }, 401);
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
