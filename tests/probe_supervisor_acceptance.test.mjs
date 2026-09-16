import assert from "node:assert/strict";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { appendFile, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON = process.env.PYTHON || "python3";
const RUNTIME = process.env.EXTELLA_ACCEPTANCE_RUNTIME || path.join(ROOT, "runtime", "probe", "entrypoint.py");
const FIXTURE = path.join(ROOT, "tests", "fixtures", "probe_acceptance_fixture.py");
const CUSTOMIZE = path.join(ROOT, "tests", "fixtures", "sitecustomize.py");
const RAW_DIR = process.env.EXTELLA_P2_RAW_DIR || path.join(os.tmpdir(), "extella-p2-acceptance-raw");
const TEST_TIMEOUT = 20_000;

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function freePort() {
  const listener = createServer();
  listener.listen(0, "127.0.0.1");
  await once(listener, "listening");
  const address = listener.address();
  const port = address.port;
  listener.close();
  await once(listener, "close");
  return port;
}

function wallRecord(label, fields = {}) {
  return {
    label,
    wall_ns: BigInt(Date.now()) * 1_000_000n,
    monotonic_ns: process.hrtime.bigint(),
    ...fields,
  };
}

async function record(rawPath, label, fields = {}) {
  const item = wallRecord(label, fields);
  await appendFile(rawPath, `${JSON.stringify(item, (_key, value) => typeof value === "bigint" ? value.toString() : value)}\n`);
}

function requestJson(port, method, requestPath, payload, timeout = 4_000) {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  return new Promise((resolve, reject) => {
    const started = process.hrtime.bigint();
    const request = http.request({
      host: "127.0.0.1",
      port,
      method,
      path: requestPath,
      headers: payload === undefined ? {} : {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
        Connection: "close",
      },
    }, response => {
      const chunks = [];
      response.on("data", chunk => chunks.push(chunk));
      response.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf8");
        let parsed;
        try {
          parsed = raw ? JSON.parse(raw) : null;
        } catch (error) {
          reject(new Error(`invalid JSON response (${response.statusCode}): ${error.message}; body=${raw.slice(0, 200)}`));
          return;
        }
        resolve({
          status: response.statusCode,
          headers: response.headers,
          body: parsed,
          elapsed_ms: Number(process.hrtime.bigint() - started) / 1e6,
        });
      });
    });
    request.setTimeout(timeout, () => request.destroy(new Error(`HTTP request timed out after ${timeout}ms`)));
    request.on("error", reject);
    if (payload !== undefined) request.write(body);
    request.end();
  });
}

async function fixtureControl(port, action, params = {}) {
  const query = new URLSearchParams(params);
  return requestJson(port, "GET", `/control/${action}?${query}`);
}

async function readFixtureEvents(eventLog) {
  const raw = await readFile(eventLog, "utf8").catch(() => "");
  return raw.trim() ? raw.trim().split("\n").map(line => JSON.parse(line)) : [];
}

async function waitFixture(port, event, count = 1, timeoutMs = 4_000) {
  const response = await fixtureControl(port, "wait", { event, count, timeout_ms: timeoutMs });
  assert.equal(response.status, 200, `fixture did not observe ${event}: ${JSON.stringify(response.body)}`);
  return response.body;
}

async function processStat(pid) {
  try {
    const stat = await readFile(`/proc/${pid}/stat`, "utf8");
    const close = stat.lastIndexOf(") ");
    const fields = stat.slice(close + 2).trim().split(/\s+/);
    const command = await readFile(`/proc/${pid}/cmdline`, "utf8").catch(() => "");
    return { pid, state: fields[0], ppid: Number(fields[1]), pgrp: Number(fields[2]), command: command.replaceAll("\0", " ").trim() };
  } catch {
    return null;
  }
}

async function procChildren(pid) {
  let childPids = [];
  try {
    const raw = await readFile(`/proc/${pid}/task/${pid}/children`, "utf8");
    childPids = raw.trim().split(/\s+/).filter(Boolean).map(Number);
  } catch {
    childPids = [];
  }
  if (childPids.length === 0) {
    for (const entry of await readdir("/proc")) {
      if (!/^\d+$/.test(entry)) continue;
      const stat = await processStat(Number(entry));
      if (stat?.ppid === pid) childPids.push(stat.pid);
    }
  }
  return (await Promise.all([...new Set(childPids)].map(processStat))).filter(Boolean);
}

async function processSnapshot(pid) {
  const children = await procChildren(pid);
  return {
    parent: await processStat(pid),
    children,
  };
}

async function processGroupMembers(groups) {
  const wanted = new Set(groups.filter(Number.isInteger).map(Number));
  if (wanted.size === 0) return [];
  const members = [];
  for (const entry of await readdir("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    const stat = await processStat(Number(entry));
    if (stat && wanted.has(stat.pgrp) && stat.state !== "Z") members.push(stat);
  }
  return members;
}

async function waitForExit(child, timeout = 2_000) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await Promise.race([
    once(child, "exit"),
    sleep(timeout).then(() => { throw new Error(`process ${child.pid} did not exit within ${timeout}ms`); }),
  ]);
}

async function terminate(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
  try {
    await waitForExit(child, 1_000);
  } catch {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await waitForExit(child, 1_000).catch(() => {});
  }
}

async function spawnFixture(directory, options = {}) {
  const eventLog = path.join(directory, "fixture-events.ndjson");
  const fixture = spawn(PYTHON, [FIXTURE, "--event-log", eventLog, "--site-mode", options.siteMode || "slow", "--provider-mode", options.providerMode || "fast"], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const ready = new Promise((resolve, reject) => {
    let output = "";
    const onData = chunk => {
      output += chunk.toString("utf8");
      const newline = output.indexOf("\n");
      if (newline === -1) return;
      fixture.stdout.off("data", onData);
      try {
        resolve(JSON.parse(output.slice(0, newline)));
      } catch (error) {
        reject(error);
      }
    };
    fixture.stdout.on("data", onData);
    fixture.once("error", reject);
    fixture.once("exit", (code, signal) => reject(new Error(`fixture exited before ready: ${code}/${signal}`)));
  });
  try {
    const info = await ready;
    return { child: fixture, port: info.port, eventLog };
  } catch (error) {
    await terminate(fixture);
    throw error;
  }
}

async function spawnSupervisor(directory, fixture, options = {}) {
  const port = await freePort();
  const env = {
    ...process.env,
    PROBE_KIND: "nu",
    PORT: String(port),
    PROBE_ALLOW_PRIVATE: "1",
    PYTHONPATH: path.dirname(CUSTOMIZE),
    EXTELLA_ACCEPTANCE_FIXTURE_URL: `http://127.0.0.1:${fixture.port}`,
    EXTELLA_ACCEPTANCE_CHILD: options.childMode || "",
    EXTELLA_ACCEPTANCE_DNS_MARKER: options.dnsMarker || "",
  };
  const supervisor = spawn(PYTHON, [RUNTIME], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
  await once(supervisor, "spawn");
  await sleep(600);
  try {
    const health = await requestJson(port, "GET", "/health", undefined, 2_000);
    assert.equal(health.status, 200, `supervisor failed to start: ${JSON.stringify(health)}`);
    await record(path.join(directory, "harness-events.ndjson"), "supervisor_ready", { pid: supervisor.pid, port, health });
    return { child: supervisor, pid: supervisor.pid, port };
  } catch (error) {
    await terminate(supervisor);
    throw error;
  }
}

async function runWithHarness(name, options, callback) {
  await mkdir(RAW_DIR, { recursive: true });
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-p2-acceptance-"));
  const rawPath = path.join(RAW_DIR, `${name}.ndjson`);
  await writeFile(rawPath, "");
  let fixture;
  let supervisor;
  try {
    fixture = await spawnFixture(directory, options);
    await record(rawPath, "fixture_ready", { pid: fixture.child.pid, port: fixture.port });
    supervisor = await spawnSupervisor(directory, fixture, options);
    await callback({ directory, rawPath, fixture, supervisor });
  } finally {
    await terminate(supervisor?.child);
    await terminate(fixture?.child);
    if (fixture?.eventLog) {
      const fixtureEvents = await readFile(fixture.eventLog, "utf8").catch(() => "");
      if (fixtureEvents) await appendFile(rawPath, fixtureEvents);
    }
    await rm(directory, { recursive: true, force: true });
  }
}

function runBody(siteUrl, timeoutMs) {
  return { site_url: siteUrl, plan: { timeout_ms: timeoutMs } };
}

async function assertNoChildren(server, rawPath, label, groups = []) {
  const snapshot = await processSnapshot(server.pid);
  await record(rawPath, label, snapshot);
  assert.deepEqual(snapshot.children, [], `${label}: supervisor retained child processes`);
  const members = await processGroupMembers(groups);
  await record(rawPath, `${label}_process_groups`, { groups, members });
  assert.deepEqual(members, [], `${label}: child process group retained live members`);
  return snapshot;
}

async function assertProbeChildren(server, rawPath, expected, label) {
  const snapshot = await processSnapshot(server.pid);
  await record(rawPath, label, snapshot);
  assert.equal(snapshot.children.length, expected, `${label}: unexpected immediate child count`);
  for (const child of snapshot.children) {
    assert.equal(child.ppid, server.pid);
    assert.match(child.command, /entrypoint\.py --probe-child nu/);
    assert.notEqual(child.state, "Z", `${label}: child is already zombie`);
  }
  return snapshot.children;
}

async function assertRuntimeProcessModel() {
  const source = await readFile(RUNTIME, "utf8");
  assert.match(source, /PROBE_CHILD_MODE\s*=\s*["']--probe-child["']/);
  assert.match(source, /start_new_session=True/);
  assert.match(source, /selectors\.DefaultSelector/);
  assert.match(source, /MAX_CHILDREN\s*=\s*2/);
  assert.equal(source.includes("EXTELLA_ACCEPTANCE"), false, "production runtime contains the test fixture backdoor");
}

test("production entrypoint cancels a slow-drip site child at remote D and reaps its PID", { timeout: TEST_TIMEOUT }, async () => {
  await assertRuntimeProcessModel();
  await runWithHarness("slow-site", { siteMode: "slow", providerMode: "fast" }, async ({ rawPath, fixture, supervisor }) => {
    const siteUrl = `http://127.0.0.1:${fixture.port}/slow-site`;
    const request = requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 300), 3_000);
    await waitFixture(fixture.port, "site_active");
    const active = await assertProbeChildren(supervisor, rawPath, 1, "slow_site_child_active");
    const result = await request;
    await record(rawPath, "slow_site_response", result);
    assert.equal(result.status, 200);
    assert.deepEqual(result.body, { status: "unavailable", reason: "timeout" });
    await waitFixture(fixture.port, "site_client_closed");
    await sleep(100);
    await assertNoChildren(supervisor, rawPath, "slow_site_children_reaped", active.map(item => item.pgrp));
    const health = await requestJson(supervisor.port, "GET", "/health");
    await record(rawPath, "slow_site_health_after_reap", health);
    assert.equal(health.status, 200);
    assert.deepEqual(health.body, { status: "ok", kind: "nu" });
  });
});

test("production child reaches a redirected slow-drip provider and cancellation closes it", { timeout: TEST_TIMEOUT }, async () => {
  await runWithHarness("slow-provider", { siteMode: "fast", providerMode: "drip" }, async ({ rawPath, fixture, supervisor }) => {
    const siteUrl = `http://127.0.0.1:${fixture.port}/fast-site`;
    const request = requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 350), 3_000);
    const active = await assertProbeChildren(supervisor, rawPath, 1, "slow_provider_child_active");
    const result = await request;
    await record(rawPath, "slow_provider_response", result);
    assert.equal(result.status, 200);
    assert.deepEqual(result.body, { status: "unavailable", reason: "timeout" });
    await waitFixture(fixture.port, "provider_active");
    await waitFixture(fixture.port, "provider_client_closed");
    await sleep(100);
    await assertNoChildren(supervisor, rawPath, "slow_provider_children_reaped", active.map(item => item.pgrp));
  });
});

test("caller source_proxy can die while the remote supervisor still stops its child at D", { timeout: TEST_TIMEOUT }, async () => {
  await runWithHarness("caller-killed", { siteMode: "slow", providerMode: "fast" }, async ({ directory, rawPath, fixture, supervisor }) => {
    const siteUrl = `http://127.0.0.1:${fixture.port}/caller-killed`;
    const planPath = path.join(directory, "plan.json");
    const outputPath = path.join(directory, "output.json");
    await writeFile(planPath, JSON.stringify({ timeout_ms: 800 }));
    const caller = spawn(PYTHON, [path.join(ROOT, "runtime", "source_proxy.py"), "NuHTML", siteUrl, planPath, outputPath], {
      cwd: ROOT,
      env: {
        ...process.env,
        EXTELLA_NU_URL: `http://127.0.0.1:${supervisor.port}/run`,
        PYTHONPATH: path.dirname(CUSTOMIZE),
        EXTELLA_ACCEPTANCE_FIXTURE_URL: `http://127.0.0.1:${fixture.port}`,
      },
      stdio: "ignore",
    });
    await waitFixture(fixture.port, "site_active");
    const active = await assertProbeChildren(supervisor, rawPath, 1, "caller_killed_child_active");
    await record(rawPath, "caller_killed_before_D", { caller_pid: caller.pid, child_pids: active.map(item => item.pid) });
    const callerKilledAt = process.hrtime.bigint();
    caller.kill("SIGKILL");
    await waitForExit(caller, 2_000);
    await record(rawPath, "caller_killed_exit", { caller_pid: caller.pid, signal: caller.signalCode, monotonic_ns: callerKilledAt });
    await sleep(1_000);
    await waitFixture(fixture.port, "site_client_closed");
    const closed = (await readFixtureEvents(fixture.eventLog)).find(item => item.event === "site_client_closed");
    assert.ok(closed, "origin did not observe the remote child connection close");
    assert.ok(BigInt(closed.monotonic_ns) >= callerKilledAt);
    await record(rawPath, "caller_killed_origin_close", closed);
    await assertNoChildren(supervisor, rawPath, "caller_killed_remote_children_reaped", active.map(item => item.pgrp));
    const health = await requestJson(supervisor.port, "GET", "/health");
    await record(rawPath, "caller_killed_health", health);
    assert.equal(health.status, 200);
    assert.equal(health.body.status, "ok");
  });
});

test("two child slots overload with HTTP 200 unavailable while health stays ready, then the next request works", { timeout: TEST_TIMEOUT }, async () => {
  await runWithHarness("overload-health", { siteMode: "slow", providerMode: "fast" }, async ({ rawPath, fixture, supervisor }) => {
    const siteUrl = `http://127.0.0.1:${fixture.port}/overload`;
    const first = requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 500), 3_000);
    const second = requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 500), 3_000);
    await waitFixture(fixture.port, "site_active", 2);
    const active = await assertProbeChildren(supervisor, rawPath, 2, "overload_two_children");
    const healthBusy = await requestJson(supervisor.port, "GET", "/health");
    await record(rawPath, "overload_health_while_busy", healthBusy);
    assert.equal(healthBusy.status, 200);
    assert.deepEqual(healthBusy.body, { status: "ok", kind: "nu" });
    const third = await requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 500), 2_000);
    await record(rawPath, "overload_third_response", third);
    assert.equal(third.status, 200);
    assert.deepEqual(third.body, { status: "unavailable", reason: "http_503" });
    const results = await Promise.all([first, second]);
    for (const result of results) {
      assert.equal(result.status, 200);
      assert.deepEqual(result.body, { status: "unavailable", reason: "timeout" });
    }
    await waitFixture(fixture.port, "all_idle");
    await sleep(100);
    await assertNoChildren(supervisor, rawPath, "overload_children_reaped", active.map(item => item.pgrp));
    const healthReady = await requestJson(supervisor.port, "GET", "/health");
    assert.equal(healthReady.status, 200);
    await fixtureControl(fixture.port, "config", { site: "fast", provider: "fast" });
    const next = await requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 1_000), 3_000);
    await record(rawPath, "overload_next_request", next);
    assert.equal(next.status, 200);
    assert.equal(next.body.schema, "extella.nu_source.v1");
    assert.equal(next.body.status, undefined);
    await assertNoChildren(supervisor, rawPath, "overload_next_request_reaped", active.map(item => item.pgrp));
  });
});

test("DNS hang is provided only by the test-local child fixture and is cancelled/reaped", { timeout: TEST_TIMEOUT }, async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "extella-p2-dns-"));
  const marker = path.join(directory, "dns-events.ndjson");
  await mkdir(RAW_DIR, { recursive: true });
  const rawPath = path.join(RAW_DIR, "dns-and-cleanup.ndjson");
  await writeFile(rawPath, "");
  let fixture;
  let supervisor;
  try {
    fixture = await spawnFixture(directory, { siteMode: "fast", providerMode: "fast" });
    supervisor = await spawnSupervisor(directory, fixture, { childMode: "dns-hang", dnsMarker: marker });
    const siteUrl = `http://127.0.0.1:${fixture.port}/dns-hang`;
    const dnsRequest = requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 250), 3_000);
    const dnsResult = await dnsRequest;
    await record(rawPath, "dns_hang_response", dnsResult);
    assert.equal(dnsResult.status, 200);
    assert.deepEqual(dnsResult.body, { status: "unavailable", reason: "timeout" });
    const markerText = await readFile(marker, "utf8");
    assert.match(markerText, /"event": "?dns_entered/);
    await record(rawPath, "dns_fixture_marker", { marker: markerText.trim() });
    await sleep(100);
    const markerPid = Number(JSON.parse(markerText.trim()).pid);
    await assertNoChildren(supervisor, rawPath, "dns_hang_children_reaped", [markerPid]);
  } finally {
    await terminate(supervisor?.child);
    await terminate(fixture?.child);
    const fixtureEvents = fixture?.eventLog ? await readFile(fixture.eventLog, "utf8").catch(() => "") : "";
    if (fixtureEvents) await appendFile(rawPath, fixtureEvents);
    await rm(directory, { recursive: true, force: true });
  }
});

test("repeated and concurrent cancellation leaves no orphan and the next request works", { timeout: TEST_TIMEOUT }, async () => {
  await runWithHarness("repeated-cleanup", { siteMode: "slow", providerMode: "fast" }, async ({ rawPath, fixture, supervisor }) => {
    const siteUrl = `http://127.0.0.1:${fixture.port}/repeated-cleanup`;
    const requests = [1, 2, 3, 4, 5].map(() => requestJson(
      supervisor.port, "POST", "/run", runBody(siteUrl, 180), 3_000,
    ));
    await waitFixture(fixture.port, "site_active", 2);
    const active = await assertProbeChildren(supervisor, rawPath, 2, "repeated_two_children");
    const repeated = await Promise.all(requests);
    await record(rawPath, "repeated_concurrent_responses", repeated);
    assert.equal(repeated.length, 5);
    assert.ok(repeated.every(result => (
      (result.status === 200 && (result.body.reason === "timeout" || result.body.reason === "http_503"))
      || (result.status === 503 && result.body.status === "error" && result.body.code === "http_503")
    )));
    for (const result of repeated.filter(item => item.status === 503)) {
      assert.equal(result.headers["content-type"], "application/json");
      assert.equal(result.headers.connection, "close");
      assert.equal(result.headers["content-length"], String(Buffer.byteLength(JSON.stringify(result.body))));
    }
    await waitFixture(fixture.port, "all_idle");
    await sleep(150);
    await assertNoChildren(supervisor, rawPath, "repeated_concurrent_children_reaped", active.map(item => item.pgrp));
    await fixtureControl(fixture.port, "config", { site: "fast", provider: "fast" });
    const next = await requestJson(supervisor.port, "POST", "/run", runBody(siteUrl, 1_000), 3_000);
    await record(rawPath, "repeated_next_request", next);
    assert.equal(next.status, 200);
    assert.equal(next.body.schema, "extella.nu_source.v1");
    await assertNoChildren(supervisor, rawPath, "repeated_next_request_reaped", active.map(item => item.pgrp));
  });
});
