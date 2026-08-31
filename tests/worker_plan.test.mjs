import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  HARD_AUDIT_TIMEOUT_MS,
  MAX_BODY_BYTES,
  auditForTest,
  machineErrorCode,
  readJson,
  runAuditForTest,
  validateRunPayload,
} from "../runtime/worker_server.mjs";
import {
  CLI_REQUEST_TIMEOUT_MS,
  cliTimeout,
  deriveSampleUrls,
  knownTransportReason,
  mergeSampleResults,
} from "../runtime/seomator/entrypoint.mjs";
import { fulfillThroughSafeFetch } from "../runtime/seomator/browser_route.mjs";

const PLAN = Object.freeze({
  max_pages: 25,
  categories: ["core", "links"],
  performance_sample_pages: 5,
  timeout_ms: 720000,
});

test("accepts the exact bounded worker schema", () => {
  assert.deepEqual(
    validateRunPayload({ site_url: "https://example.com/", plan: PLAN }),
    { siteUrl: "https://example.com/", plan: PLAN },
  );
});

test("rejects untrusted URL and plan variants", () => {
  const cases = [
    { site_url: "https://user:password@example.com/", plan: PLAN },
    { site_url: "file:///etc/passwd", plan: PLAN },
    { site_url: "https://example.com/", extra: true, plan: PLAN },
    { site_url: "https://example.com/", plan: { ...PLAN, max_pages: 101 } },
    { site_url: "https://example.com/", plan: { ...PLAN, categories: ["unknown"] } },
    { site_url: "https://example.com/", plan: { ...PLAN, performance_sample_pages: 6 } },
    { site_url: "https://example.com/", plan: { ...PLAN, timeout_ms: 720001 } },
  ];
  for (const body of cases) assert.throws(() => validateRunPayload(body), /invalid/);
});

test("body-size protection rejects an oversized request before parsing", async () => {
  const request = {
    async *[Symbol.asyncIterator]() {
      yield Buffer.alloc(MAX_BODY_BYTES + 1, "x");
    },
  };
  await assert.rejects(readJson(request), /body too large/);
});

test("worker returns only a fixed machine error code", () => {
  assert.equal(machineErrorCode(Object.assign(new Error("ignored"), { code: "waf" })), "waf");
  assert.equal(machineErrorCode(new Error("audit timed out")), "timeout");
  assert.equal(machineErrorCode(new Error("upstream secret text")), "audit_failed");
});

test("SEOmator entrypoint classifies only fixed transport reasons", () => {
  for (const reason of ["waf", "captcha", "http_403", "http_429", "http_503", "robots_denied", "timeout"]) {
    const payload = reason.startsWith("http_") ? { status_code: Number(reason.slice(5)) } : { error: { code: reason } };
    assert.equal(knownTransportReason(payload), reason);
  }
  assert.equal(knownTransportReason({ message: "waf captcha 403" }), null);
});

test("Playwright traffic is fulfilled through safe fetch without browser DNS", async () => {
  let continued = false;
  let fulfilled;
  let aborted = false;
  let fetchInit;
  const route = {
    request: () => ({ method: () => "GET", url: () => "https://rebind.test/", headers: () => ({}) }),
    continue: async () => { continued = true; },
    fulfill: async value => { fulfilled = value; },
    abort: async () => { aborted = true; },
  };
  await fulfillThroughSafeFetch(route, async (_url, init) => {
    fetchInit = init;
    return new Response("safe", {
      status: 200,
      headers: { "content-type": "text/plain", "set-cookie": "blocked=1" },
    });
  });
  assert.equal(continued, false);
  assert.equal(aborted, false);
  assert.equal(fulfilled.status, 200);
  assert.equal(fulfilled.headers["set-cookie"], undefined);
  assert.equal(fulfilled.body.toString(), "safe");
  assert.equal(fetchInit.redirect, "manual");

  await fulfillThroughSafeFetch(route, async () => new Response(null, {
    status: 302,
    headers: { location: "https://next.test/path" },
  }));
  assert.equal(continued, false);
  assert.equal(fulfilled.status, 302);
  assert.equal(fulfilled.headers.location, "https://next.test/path");

  fulfilled = undefined;
  await fulfillThroughSafeFetch(route, async () => { throw new TypeError("DNS rebound to private address"); });
  assert.equal(continued, false);
  assert.equal(aborted, true);
  assert.equal(fulfilled, undefined);
});

test("audit passes a private plan file and removes it with the work directory", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-worker-test-"));
  const entrypoint = path.join(directory, "entrypoint.mjs");
  const observed = path.join(directory, "observed.json");
  await writeFile(entrypoint, [
    'import { readFileSync, writeFileSync } from "node:fs";',
    'writeFileSync(process.env.EXTELLA_TEST_OBSERVED, readFileSync(process.argv[4]));',
    'writeFileSync(process.argv[3], JSON.stringify({ crawledPages: 25, categoryResults: [] }));',
  ].join("\n"));
  const previous = process.env.EXTELLA_TEST_OBSERVED;
  process.env.EXTELLA_TEST_OBSERVED = observed;
  try {
    const result = await auditForTest("https://example.com/", PLAN, undefined, entrypoint);
    assert.equal(result.crawledPages, 25);
    assert.deepEqual(JSON.parse(await readFile(observed, "utf8")), PLAN);
  } finally {
    if (previous === undefined) delete process.env.EXTELLA_TEST_OBSERVED;
    else process.env.EXTELLA_TEST_OBSERVED = previous;
  }
});

test("hard cap cannot be raised and cancellation kills the spawned audit", async () => {
  assert.equal(HARD_AUDIT_TIMEOUT_MS, 900000);
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-worker-cancel-"));
  const entrypoint = path.join(directory, "sleep.mjs");
  await writeFile(entrypoint, 'setTimeout(() => {}, 60000);');
  const controller = new AbortController();
  const cancelled = runAuditForTest(
    "https://example.com/",
    path.join(directory, "report.json"),
    path.join(directory, "plan.json"),
    1,
    controller.signal,
    entrypoint,
  );
  controller.abort();
  await assert.rejects(cancelled, /audit cancelled/);
});

test("response-size protection rejects an oversized report", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-worker-size-"));
  const entrypoint = path.join(directory, "large.mjs");
  await writeFile(entrypoint, [
    'import { writeFileSync } from "node:fs";',
    'writeFileSync(process.argv[3], "x".repeat(10000001));',
  ].join("\n"));
  await assert.rejects(auditForTest("https://example.com/", PLAN, undefined, entrypoint), /report too large/);
});

test("report size is checked before the report is read", async () => {
  const source = await readFile(new URL("../runtime/worker_server.mjs", import.meta.url), "utf8");
  assert.ok(source.indexOf("const metadata = await stat(outputPath)") < source.indexOf("const raw = await readFile(outputPath)"));
});

test("SEOmator selects a deterministic homepage-first sample and merges only executed expensive categories", () => {
  assert.equal(CLI_REQUEST_TIMEOUT_MS, 120000);
  assert.equal(cliTimeout(720000), 120000);
  const urls = deriveSampleUrls({
    pages: [
      { url: "https://example.com/deep/path", depth: 3 },
      { url: "https://example.com/alpha", depth: 1 },
      { url: "https://example.com/shallow", depth: 99 },
      { url: "https://outside.example/", depth: 0 },
    ],
    categoryResults: [{ categoryId: "core", results: [
      { details: { pageUrl: "https://example.com/zeta" } },
      { status: "warn", ruleId: "core-robots-meta", details: { pageUrl: "https://example.com/blocked", allDirectives: [{ source: "meta", directives: ["noindex"] }] } },
      { status: "fail", ruleId: "technical-server-error", details: { pageUrl: "https://example.com/error/deep" } },
      { status: "fail", ruleId: "technical-4xx-non-404", details: { pageUrl: "https://example.com/gone" } },
      { status: "fail", ruleId: "technical-bad-content-type", details: { pageUrl: "https://example.com/api" } },
    ] }],
  }, "https://example.com/", 5);
  assert.deepEqual(urls, ["https://example.com/", "https://example.com/alpha", "https://example.com/shallow", "https://example.com/zeta", "https://example.com/deep/path"]);
  assert.equal(urls.includes("https://example.com/blocked"), false);
  assert.equal(urls.includes("https://example.com/error/deep"), false);
  assert.equal(urls.includes("https://example.com/gone"), false);
  assert.equal(urls.includes("https://example.com/api"), false);
  const categoryResults = mergeSampleResults(
    { categoryResults: [{ categoryId: "core", results: [{ status: "pass" }], score: 90 }] },
    ["core"],
    [
      { categoryResults: [{ categoryId: "perf", results: [{ status: "fail" }], score: 50 }, { categoryId: "js", results: [{ status: "warn" }], score: 70 }] },
      { categoryResults: [{ categoryId: "perf", results: [{ status: "pass" }], score: 100 }, { categoryId: "js", results: [{ status: "pass" }], score: 100 }] },
    ],
    ["perf", "js"],
  );
  assert.deepEqual(categoryResults.map(category => category.categoryId), ["core", "perf", "js"]);
  assert.equal(categoryResults[1].results.length, 2);
  assert.equal(categoryResults[2].warnCount, 1);
  assert.equal(mergeSampleResults({ categoryResults: [] }, ["core"], [], ["perf"]), null);
});

test("Linux cancellation kills the detached process group including a grandchild", { skip: process.platform !== "linux" }, async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-worker-group-"));
  const entrypoint = path.join(directory, "group.mjs");
  const pidPath = path.join(directory, "grandchild.pid");
  await writeFile(entrypoint, [
    'import { spawn } from "node:child_process";',
    'import { writeFileSync } from "node:fs";',
    `const grandchild = spawn("sh", ["-c", "exec sleep 60"], { stdio: "ignore" });`,
    `writeFileSync(${JSON.stringify(pidPath)}, String(grandchild.pid));`,
    'setInterval(() => {}, 60000);',
  ].join("\n"));
  const controller = new AbortController();
  try {
    const cancelled = runAuditForTest("https://example.com/", path.join(directory, "report.json"), path.join(directory, "plan.json"), 60000, controller.signal, entrypoint);
    let pid;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        pid = Number(await readFile(pidPath, "utf8"));
        break;
      } catch {
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    }
    assert.ok(Number.isInteger(pid) && pid > 1, "grandchild PID was not recorded");
    controller.abort();
    await assert.rejects(cancelled, /audit cancelled/);
    let alive = true;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        process.kill(pid, 0);
      } catch {
        alive = false;
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.equal(alive, false, "grandchild survived process-group cancellation");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("Linux timer kills the detached process group including a grandchild", { skip: process.platform !== "linux" }, async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-worker-timer-"));
  const entrypoint = path.join(directory, "group.mjs");
  const pidPath = path.join(directory, "grandchild.pid");
  await writeFile(entrypoint, [
    'import { spawn } from "node:child_process";',
    'import { writeFileSync } from "node:fs";',
    'const grandchild = spawn("sh", ["-c", "exec sleep 60"], { stdio: "ignore" });',
    `writeFileSync(${JSON.stringify(pidPath)}, String(grandchild.pid));`,
    'setInterval(() => {}, 60000);',
  ].join("\n"));
  try {
    const timedOut = runAuditForTest("https://example.com/", path.join(directory, "report.json"), path.join(directory, "plan.json"), 50, undefined, entrypoint);
    let pid;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        pid = Number(await readFile(pidPath, "utf8"));
        break;
      } catch {
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    }
    assert.ok(Number.isInteger(pid) && pid > 1, "grandchild PID was not recorded");
    await assert.rejects(timedOut, /audit timed out/);
    let alive = true;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        process.kill(pid, 0);
      } catch {
        alive = false;
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.equal(alive, false, "grandchild survived timer process-group cancellation");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
