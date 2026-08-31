import http from "node:http";
import https from "node:https";
import { lookup as dnsLookup } from "node:dns/promises";
import net from "node:net";

const DEFAULT_MAX_REDIRECTS = 5;
const MAX_RESPONSE_BYTES = 10_000_000;
const MAX_IN_MEMORY_HEADS = 256;
const CREDENTIAL_HEADERS = new Set(["authorization", "cookie", "proxy-authorization"]);

function fail(message) {
  throw new TypeError(`unsafe fetch target: ${message}`);
}

function ipv4Octets(address) {
  if (net.isIP(address) !== 4) return null;
  return address.split(".").map(Number);
}

export function isPublicGlobalAddress(address) {
  const octets = ipv4Octets(address);
  if (octets) {
    const [a, b, c, d] = octets;
    if (
      a === 0 || a === 10 || a === 127 || a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && (b === 0 || b === 2 || b === 168
        || (b === 31 && c === 196) || (b === 52 && c === 193)
        || (b === 88 && c === 99) || (b === 175 && c === 48))) ||
      (a === 198 && (b === 18 || b === 19 || b === 51)) ||
      (a === 203 && b === 0 && c === 113) ||
      (a === 100 && b === 100 && c === 100 && d === 200)
    ) return false;
    return true;
  }

  const normalized = address.replace(/^\[|\]$/g, "").toLowerCase();
  if (net.isIP(normalized) !== 6 || normalized.includes("%")) return false;
  const value = ipv6Value(normalized);
  if (value === null) return false;

  if (inIpv6Cidr(value, "::ffff:0:0", 96)) {
    return isPublicGlobalAddress(ipv4FromValue(value & 0xffffffffn));
  }

  // Global IPv6 unicast is 2000::/3. Everything else is local, special-use,
  // multicast, or reserved and must never be an outbound audit target.
  if ((value >> 125n) !== 1n) return false;
  if (inIpv6Cidr(value, "2001:db8::", 32) || inIpv6Cidr(value, "2001:2::", 48)
      || inIpv6Cidr(value, "2001:10::", 28) || inIpv6Cidr(value, "2001:20::", 28)) return false;

  // 6to4 and Teredo embed an IPv4 endpoint. Reject them when that endpoint is
  // non-public too, rather than letting an IPv6 wrapper bypass IPv4 policy.
  if (inIpv6Cidr(value, "2002::", 16)) {
    return isPublicGlobalAddress(ipv4FromValue((value >> 80n) & 0xffffffffn));
  }
  if (inIpv6Cidr(value, "2001::", 32)) {
    return isPublicGlobalAddress(ipv4FromValue((~value) & 0xffffffffn));
  }
  return true;
}

function ipv4FromValue(value) {
  return [24n, 16n, 8n, 0n].map(shift => Number((value >> shift) & 0xffn)).join(".");
}

function ipv6Value(address) {
  const dotted = address.lastIndexOf(":");
  if (dotted >= 0 && address.slice(dotted + 1).includes(".")) {
    const embedded = ipv4Octets(address.slice(dotted + 1));
    if (!embedded) return null;
    address = `${address.slice(0, dotted)}:${((embedded[0] << 8) | embedded[1]).toString(16)}:${((embedded[2] << 8) | embedded[3]).toString(16)}`;
  }
  const parts = address.split("::");
  if (parts.length > 2) return null;
  const left = parts[0] ? parts[0].split(":") : [];
  const right = parts.length === 2 && parts[1] ? parts[1].split(":") : [];
  if (left.length + right.length > 8 || (parts.length === 1 && left.length !== 8)) return null;
  const groups = parts.length === 2
    ? [...left, ...Array(8 - left.length - right.length).fill("0"), ...right]
    : left;
  if (groups.some(group => !/^[0-9a-f]{1,4}$/i.test(group))) return null;
  return groups.reduce((value, group) => (value << 16n) | BigInt(`0x${group}`), 0n);
}

function inIpv6Cidr(value, network, prefix) {
  const base = ipv6Value(network);
  if (base === null) return false;
  const mask = ((1n << BigInt(prefix)) - 1n) << BigInt(128 - prefix);
  return (value & mask) === (base & mask);
}

function targetUrl(value) {
  let url;
  try {
    url = value instanceof URL ? new URL(value.toString()) : new URL(value);
  } catch {
    fail("URL is invalid");
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || !url.hostname) fail("protocol or hostname is invalid");
  if (url.username || url.password) fail("URL credentials are forbidden");
  return url;
}

function ensureNoCredentials(request) {
  if (request.credentials === "include") fail("request credentials are forbidden");
  for (const name of request.headers.keys()) {
    if (CREDENTIAL_HEADERS.has(name.toLowerCase())) fail("credential headers are forbidden");
  }
}

export async function resolvePublicAddresses(url, lookupImpl = dnsLookup) {
  const hostname = url instanceof URL ? url.hostname : targetUrl(url).hostname;
  const resolved = await lookupImpl(hostname, { all: true, verbatim: true });
  const addresses = [...new Set((Array.isArray(resolved) ? resolved : [resolved]).map(item =>
    typeof item === "string" ? item : item?.address,
  ))];
  if (!addresses.length || addresses.some(address => typeof address !== "string" || !isPublicGlobalAddress(address))) {
    fail("DNS result is not entirely public");
  }
  return addresses;
}

function safeHeaders(headers, url, body) {
  const result = new Headers(headers);
  for (const name of CREDENTIAL_HEADERS) result.delete(name);
  result.delete("host");
  result.delete("transfer-encoding");
  if (body === null) result.delete("content-length");
  else result.set("content-length", String(body.length));
  result.set("host", url.host);
  return Object.fromEntries(result.entries());
}

async function requestFromValidatedAddress({ url, address, method, headers, body, signal }) {
  const transport = url.protocol === "https:" ? https : http;
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  const port = url.port ? Number(url.port) : (url.protocol === "https:" ? 443 : 80);
  return new Promise((resolve, reject) => {
    const request = transport.request({
      hostname: address,
      port,
      method,
      path: `${url.pathname}${url.search}`,
      headers: safeHeaders(headers, url, body),
      agent: false,
      servername: url.protocol === "https:" && net.isIP(hostname) === 0 ? hostname : undefined,
      rejectUnauthorized: true,
    }, response => {
      const chunks = [];
      let size = 0;
      response.on("data", chunk => {
        size += chunk.length;
        if (size > MAX_RESPONSE_BYTES) {
          request.destroy(new Error("response too large"));
          return;
        }
        chunks.push(chunk);
      });
      response.once("error", reject);
      response.once("end", () => {
        const status = response.statusCode ?? 502;
        const responseBody = [204, 205, 304].includes(status) ? null : Buffer.concat(chunks);
        resolve(new Response(responseBody, {
          status,
          statusText: response.statusMessage,
          headers: response.headers,
        }));
      });
    });
    request.setTimeout(30_000, () => request.destroy(new Error("request timed out")));
    request.once("error", reject);
    if (signal) {
      const abort = () => request.destroy(signal.reason instanceof Error ? signal.reason : new Error("request aborted"));
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    }
    if (body !== null) request.write(body);
    request.end();
  });
}

function redirectMethod(status, method) {
  return status === 303 || ((status === 301 || status === 302) && method === "POST") ? "GET" : method;
}

export function createSafeFetch({ lookupImpl = dnsLookup, fetchImpl = requestFromValidatedAddress, maxRedirects = DEFAULT_MAX_REDIRECTS } = {}) {
  if (!Number.isInteger(maxRedirects) || maxRedirects < 0 || maxRedirects > 10) {
    throw new TypeError("safe fetch redirect limit is invalid");
  }
  const cache = new Map();
  return async function safeFetch(input, init) {
    const request = input instanceof Request ? new Request(input, init) : new Request(input, init);
    ensureNoCredentials(request);
    const cacheKey = request.method === "HEAD"
      ? JSON.stringify([request.method, request.url, [...request.headers].sort(([left], [right]) => left.localeCompare(right))])
      : null;
    if (cacheKey && cache.has(cacheKey)) return (await cache.get(cacheKey)).clone();
    const pending = (async () => {
      let url = targetUrl(request.url);
      let method = request.method;
      let headers = new Headers(request.headers);
      let body = ["GET", "HEAD"].includes(method) ? null : Buffer.from(await request.arrayBuffer());
      let redirects = 0;
      while (true) {
        const addresses = await resolvePublicAddresses(url, lookupImpl);
        const response = await fetchImpl({ url, address: addresses[0], method, headers, body, signal: request.signal });
        const location = response.headers?.get("location");
        if (![301, 302, 303, 307, 308].includes(response.status) || !location) return response;
        if (request.redirect === "manual") return response;
        if (request.redirect === "error") throw new TypeError("unsafe fetch target: redirect is forbidden");
        if (redirects >= maxRedirects) throw new TypeError("unsafe fetch target: redirect limit exceeded");
        redirects += 1;
        url = targetUrl(new URL(location, url));
        const nextMethod = redirectMethod(response.status, method);
        if (nextMethod === "GET" && method !== "GET") {
          headers.delete("content-length");
          headers.delete("content-type");
          body = null;
        }
        method = nextMethod;
      }
    })();
    if (!cacheKey) return pending;
    cache.set(cacheKey, pending);
    if (cache.size > MAX_IN_MEMORY_HEADS) cache.delete(cache.keys().next().value);
    try {
      return (await pending).clone();
    } catch (error) {
      cache.delete(cacheKey);
      throw error;
    }
  };
}

let installed = false;

export function installSafeFetch() {
  if (!installed) {
    globalThis.fetch = createSafeFetch();
    installed = true;
  }
  return globalThis.fetch;
}

if (process.env.EXTELLA_SAFE_FETCH_PRELOAD === "1") installSafeFetch();
