const DEFAULT_BACKEND = "https://kirana-saathi-api.onrender.com";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  try {
    const { path } = await context.params;
    const incoming = new URL(request.url);
    const backend = (process.env.KIRANA_BACKEND_URL || DEFAULT_BACKEND).replace(/\/$/, "");
    const safePath = path.map(encodeURIComponent).join("/");
    const target = `${backend}/api/${safePath}${incoming.search}`;

    const headers = new Headers(request.headers);
    for (const name of ["host", "connection", "content-length", "accept-encoding"]) {
      headers.delete(name);
    }

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });

    // Buffer the upstream response before returning it. This prevents the
    // premature stream closures observed between Render and its edge proxy.
    const body = await upstream.arrayBuffer();
    const responseHeaders = new Headers(upstream.headers);
    for (const name of ["connection", "content-length", "content-encoding", "transfer-encoding"]) {
      responseHeaders.delete(name);
    }
    responseHeaders.set("Cache-Control", "no-store");

    return new Response(body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("[KiranaSaathi proxy]", error);
    return Response.json(
      { detail: "The application server could not reach the KiranaSaathi API." },
      { status: 502 },
    );
  }
}

export const dynamic = "force-dynamic";
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
