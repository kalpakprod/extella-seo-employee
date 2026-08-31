const OMIT_RESPONSE_HEADERS = new Set(["connection", "content-length", "set-cookie", "transfer-encoding"]);

export async function fulfillThroughSafeFetch(route, fetchImpl = globalThis.fetch) {
  const request = route.request();
  const method = request.method();
  if (!["GET", "HEAD"].includes(method)) {
    await route.abort("blockedbyclient");
    return;
  }
  try {
    const response = await fetchImpl(request.url(), {
      method,
      headers: request.headers(),
      redirect: "manual",
      credentials: "omit",
    });
    const headers = Object.fromEntries(
      [...response.headers].filter(([name]) => !OMIT_RESPONSE_HEADERS.has(name.toLowerCase())),
    );
    const body = method === "HEAD" ? Buffer.alloc(0) : Buffer.from(await response.arrayBuffer());
    await route.fulfill({ status: response.status, headers, body });
  } catch {
    await route.abort("blockedbyclient");
  }
}
