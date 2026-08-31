import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { lookup } from "node:dns/promises";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";

export const MAX_BODY_BYTES = 4096;
export const MAX_REPORT_BYTES = 10_000_000;
export const HARD_AUDIT_TIMEOUT_MS = 900_000;
export const SEO_CATEGORIES = Object.freeze([
  "core", "technical", "perf", "links", "images", "security", "crawl", "schema", "a11y", "content",
  "social", "eeat", "url", "mobile", "i18n", "legal", "js", "redirect", "htmlval", "geo",
]);
const SEO_CATEGORY_SET = new Set(SEO_CATEGORIES);
const ENTRYPOINT = "/app/extella_entrypoint.mjs";

function workerConfig() {
  const kind = process.env.EXTELLA_WORKER_KIND;
  const port = Number(process.env.EXTELLA_WORKER_PORT);
  if (!['CrawlSEO', 'SEOmator', 'Resolver'].includes(kind) || !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("worker configuration is invalid");
  }
  return { kind, port };
}

function isReady(kind) {
  if (kind === "Resolver") return true;
  if (kind === "CrawlSEO") {
    try {
      const databaseUrl = new URL(process.env.DATABASE_URL || "");
      return databaseUrl.protocol === "postgresql:" && databaseUrl.hostname.length > 0;
    } catch {
      return false;
    }
  }
  return process.env.PLAYWRIGHT_BROWSERS_PATH === "/ms-playwright" && existsSync("/app/dist/cli.js");
}

function responseIsWritable(response) {
  return !response.destroyed && !response.writableEnded && !response.writableFinished;
}

function responseHasPendingWork(response) {
  return !response.writableEnded && !response.writableFinished;
}

function send(response, status, payload) {
  if (!responseIsWritable(response)) return false;
  try {
    const body = Buffer.from(JSON.stringify(payload));
    response.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": body.length,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(body);
    return true;
  } catch {
    return false;
  }
}

export function machineErrorCode(error) {
  if (error && typeof error === "object" && typeof error.code === "string") {
    if (["waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"].includes(error.code)) {
      return error.code;
    }
  }
  return error instanceof Error && error.message === "audit timed out" ? "timeout" : "audit_failed";
}

export async function readJson(request, signal) {
  const read = (async () => {
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) throw new Error("body too large");
      chunks.push(chunk);
    }
    const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("body is invalid");
    return value;
  })();
  if (!signal) return read;
  if (signal.aborted) throw new Error("request disconnected");
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => reject(new Error("request disconnected"));
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([read, aborted]);
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}

function validatePlan(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("plan is invalid");
  if (Object.keys(value).sort().join() !== "categories,max_pages,performance_sample_pages,timeout_ms") {
    throw new Error("plan is invalid");
  }
  const { max_pages: maxPages, categories, performance_sample_pages: samplePages, timeout_ms: timeoutMs } = value;
  if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 100) throw new Error("plan is invalid");
  if (!Array.isArray(categories) || categories.length === 0 || new Set(categories).size !== categories.length) {
    throw new Error("plan is invalid");
  }
  if (categories.some(category => typeof category !== "string" || !SEO_CATEGORY_SET.has(category))) {
    throw new Error("plan is invalid");
  }
  if (!Number.isInteger(samplePages) || samplePages < 1 || samplePages > 5 || samplePages > maxPages) {
    throw new Error("plan is invalid");
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 720_000) throw new Error("plan is invalid");
  return Object.freeze({ max_pages: maxPages, categories: [...categories], performance_sample_pages: samplePages, timeout_ms: timeoutMs });
}

export function validateRunPayload(value) {
  if (Object.keys(value).sort().join() !== "plan,site_url") throw new Error("body is invalid");
  if (typeof value.site_url !== "string") throw new Error("URL is invalid");
  const parsed = new URL(value.site_url);
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) {
    throw new Error("URL is invalid");
  }
  return { siteUrl: parsed.toString(), plan: validatePlan(value.plan) };
}

function killAuditProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "linux" && child.pid) {
    try {
      process.kill(-child.pid, "SIGKILL");
      return;
    } catch {
      // Fall back to the direct child if the process group is unavailable.
    }
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // The exit event still settles the audit when the process raced the kill.
  }
}

function runAuditProcess(siteUrl, outputPath, planPath, timeoutMs, signal, entrypoint) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("audit cancelled"));
      return;
    }
    let child;
    try {
      const entrypointArgs = process.env.EXTELLA_WORKER_KIND === "CrawlSEO"
        ? [process.env.TSX_CLI || "/runner/node_modules/tsx/dist/cli.mjs", entrypoint, siteUrl, outputPath, planPath]
        : [entrypoint, siteUrl, outputPath, planPath];
      child = spawn("node", entrypointArgs, {
      cwd: process.env.EXTELLA_WORKER_KIND
        ? (process.env.EXTELLA_WORKER_KIND === "CrawlSEO" ? "/app" : "/work")
        : undefined,
      env: process.env,
      stdio: "ignore",
      detached: process.platform === "linux",
      });
    } catch (error) {
      reject(error);
      return;
    }
    let settled = false;
    let terminated = false;
    let cancellationReason = null;
    let timer;
    const terminate = () => {
      if (settled || terminated) return;
      terminated = true;
      cancellationReason ??= "cancelled";
      killAuditProcess(child);
    };
    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", terminate);
      if (cancellationReason === "cancelled") reject(new Error("audit cancelled"));
      else if (cancellationReason === "timed_out") reject(new Error("audit timed out"));
      else if (error) reject(error);
      else resolve();
    };
    child.once("error", finish);
    child.once("exit", (code, childSignal) => {
      finish(code === 0 && childSignal === null ? null : new Error("audit failed"));
    });
    signal?.addEventListener("abort", terminate, { once: true });
    timer = setTimeout(() => {
      if (terminated) return;
      cancellationReason = "timed_out";
      terminate();
    }, Math.min(timeoutMs, HARD_AUDIT_TIMEOUT_MS));
    if (signal?.aborted) terminate();
  });
}

export function runAudit(siteUrl, outputPath, planPath, timeoutMs, signal) {
  return runAuditProcess(siteUrl, outputPath, planPath, timeoutMs, signal, ENTRYPOINT);
}

export function runAuditForTest(siteUrl, outputPath, planPath, timeoutMs, signal, entrypoint) {
  return runAuditProcess(siteUrl, outputPath, planPath, timeoutMs, signal, entrypoint);
}

async function auditProcess(siteUrl, plan, signal, entrypoint) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-seo-"));
  const outputPath = path.join(directory, "report.json");
  const planPath = path.join(directory, "plan.json");
  try {
    await writeFile(planPath, `${JSON.stringify(plan)}\n`, { encoding: "utf8", mode: 0o600 });
    await runAuditProcess(siteUrl, outputPath, planPath, plan.timeout_ms, signal, entrypoint);
    const metadata = await stat(outputPath);
    if (metadata.size > MAX_REPORT_BYTES) throw new Error("report too large");
    const raw = await readFile(outputPath);
    const payload = JSON.parse(raw.toString("utf8"));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("report invalid");
    return payload;
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

export function audit(siteUrl, plan, signal) {
  return auditProcess(siteUrl, plan, signal, ENTRYPOINT);
}

export function auditForTest(siteUrl, plan, signal, entrypoint) {
  return auditProcess(siteUrl, plan, signal, entrypoint);
}

function watchDisconnect(request, response, controller) {
  const onDisconnect = () => {
    if (responseHasPendingWork(response)) controller.abort();
  };
  request.once("aborted", onDisconnect);
  request.once("error", onDisconnect);
  response.once("close", onDisconnect);
  response.once("error", onDisconnect);
  return () => {
    request.removeListener("aborted", onDisconnect);
    request.removeListener("error", onDisconnect);
    response.removeListener("close", onDisconnect);
    response.removeListener("error", onDisconnect);
  };
}

function rejectResolverRun(kind, response) {
  if (kind === "Resolver") {
    send(response, 404, { status: "error", code: "route_not_found" });
    return true;
  }
  return false;
}

function createHandler(kind) {
  let busy = false;
  return async function handleRequest(request, response) {
    if (request.method === "GET" && request.url === "/health") {
      const ready = isReady(kind);
      send(response, ready ? 200 : 503, { status: ready ? "ok" : "not_ready", source: kind, ready });
      return;
    }
    if (request.method === "POST" && request.url === "/resolve") {
      try {
        const body = await readJson(request);
        if (Object.keys(body).join() !== "hostname" || typeof body.hostname !== "string" || !/^[A-Za-z0-9.-]{1,253}$/.test(body.hostname)) {
          throw new Error("hostname is invalid");
        }
        const addresses = [...new Set((await lookup(body.hostname, { all: true, verbatim: true })).map(item => item.address))];
        send(response, 200, { addresses });
      } catch {
        send(response, 400, { status: "error", code: "resolve_failed" });
      }
      return;
    }
    if (rejectResolverRun(kind, response)) return;
    if (request.method !== "POST" || request.url !== "/run") {
      send(response, 404, { status: "error", code: "route_not_found" });
      return;
    }
    if (busy) {
      send(response, 409, { status: "error", code: "worker_busy" });
      return;
    }
    busy = true;
    const controller = new AbortController();
    const stopWatching = watchDisconnect(request, response, controller);
    try {
      const { siteUrl, plan } = validateRunPayload(await readJson(request, controller.signal));
      const result = await audit(siteUrl, plan, controller.signal);
      if (!controller.signal.aborted) send(response, 200, result);
    } catch (error) {
      if (!controller.signal.aborted) send(response, 400, { status: "error", code: machineErrorCode(error) });
    } finally {
      stopWatching();
      busy = false;
    }
  };
}

export function startServer(config = workerConfig()) {
  const handleRequest = createHandler(config.kind);
  const server = createServer((request, response) => {
    request.once("error", () => {});
    response.once("error", () => {});
    void handleRequest(request, response).catch(() => {
      send(response, 500, { status: "error", code: "worker_failed" });
    });
  });
  server.headersTimeout = 5_000;
  server.requestTimeout = 10_000;
  server.keepAliveTimeout = 5_000;
  server.listen(config.port, "0.0.0.0");
  return server;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) startServer();
