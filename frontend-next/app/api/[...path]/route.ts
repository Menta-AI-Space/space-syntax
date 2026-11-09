// Use an explicit IPv4 loopback fallback. On some Windows setups, `localhost`
// resolves to IPv6 first while uvicorn only listens on IPv4.
const BACKEND_API_BASE_URL = (process.env.BACKEND_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

// Server-only: never prefix with NEXT_PUBLIC_, or the token ships to the browser
// and the auth it buys is worthless. Unset in local dev, where the backend
// leaves auth disabled.
const BACKEND_API_TOKEN = process.env.BACKEND_API_TOKEN || "";

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const incomingUrl = new URL(request.url);
  const targetUrl = `${BACKEND_API_BASE_URL}/api/${path.join("/")}${incomingUrl.search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("connection");
  // Drop any client-supplied key, then attach the real one from server env, so a
  // caller can't smuggle their own through the proxy.
  headers.delete("x-api-key");
  if (BACKEND_API_TOKEN) headers.set("x-api-key", BACKEND_API_TOKEN);
  // Drop any client-supplied X-Forwarded-For rather than relaying it: nothing
  // sits in front of this server (see docker-compose.yml) to overwrite it, so
  // relaying the caller's own header would let them pick their own rate-limit
  // bucket on every request. The backend falls back to the connecting socket
  // address (this proxy's own), which the caller cannot forge per-request.
  headers.delete("x-forwarded-for");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const controller = new AbortController();
  // Kept in sync with DEFAULT_TIMEOUT_MS in frontend-next/lib/api.ts — this
  // proxy must not give up before the backend's own worst-case budget does.
  const timeout = setTimeout(() => controller.abort(), 330_000);
  const abortFromClient = () => controller.abort();
  request.signal.addEventListener("abort", abortFromClient, { once: true });

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: controller.signal,
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    responseHeaders.delete("transfer-encoding");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "AbortError";
    // 이 detail이 화면에 그대로 뜬다(지도 탭 「Backend service unavailable」) — 사용자 말로,
    // lib/api.ts의 브라우저 쪽 실패 문구와 같게 (2026-11-06 UX 자동 개선).
    return Response.json(
      {
        detail: timedOut
          ? "요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
          : "분석 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      },
      { status: timedOut ? 504 : 502 },
    );
  } finally {
    clearTimeout(timeout);
    request.signal.removeEventListener("abort", abortFromClient);
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
