import { createClient } from "@supabase/supabase-js";



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

    if (
      !ticker
      || !/^[A-Z0-9.^_-]{1,30}$/.test(ticker)
    ) {
      return jsonResponse(
        {
          ok: false,
          error: "Invalid ticker",
        },
        400,
      );
    }

    if (!["1d", "1w"].includes(timeframe)) {
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

    return jsonResponse(
      {
        ok: false,
        error:
          error instanceof Error
            ? error.message
            : "Unknown server error",
      },
      500,
    );
  }
});
