import assert from "node:assert/strict";
import test from "node:test";

import { createSafeFetch } from "../runtime/safe_fetch.mjs";

function resolver(records) {
  return async hostname => (records[hostname] || []).map(address => ({ address }));
}

test("blocks a public-to-private redirect before a second fetch", async () => {
  const calls = [];
  const safeFetch = createSafeFetch({
    lookupImpl: resolver({ "public.test": ["93.184.216.34"], "private.test": ["127.0.0.1"] }),
    fetchImpl: async ({ url }) => {
      calls.push(url.href);
      return new Response(null, { status: 302, headers: { location: "http://private.test/metadata" } });
    },
  });

  await assert.rejects(safeFetch("https://public.test/"), /DNS result is not entirely public/);
  assert.deepEqual(calls, ["https://public.test/"]);
});

test("blocks a hostname when any DNS answer is non-public", async () => {
  let calls = 0;
  const safeFetch = createSafeFetch({
    lookupImpl: resolver({ "mixed.test": ["93.184.216.34", "169.254.169.254"] }),
    fetchImpl: async () => {
      calls += 1;
      return new Response("unexpected");
    },
  });

  await assert.rejects(safeFetch("https://mixed.test/"), /DNS result is not entirely public/);
  assert.equal(calls, 0);
});

test("follows a redirect only after validating the public next hop", async () => {
  const calls = [];
  const safeFetch = createSafeFetch({
    lookupImpl: resolver({ "first.test": ["93.184.216.34"], "second.test": ["1.1.1.1"] }),
    fetchImpl: async ({ url }) => {
      calls.push(url.href);
      return url.hostname === "first.test"
        ? new Response(null, { status: 302, headers: { location: "https://second.test/final" } })
        : new Response("ok", { status: 200 });
    },
  });

  const response = await safeFetch("https://first.test/");
  assert.equal(await response.text(), "ok");
  assert.deepEqual(calls, ["https://first.test/", "https://second.test/final"]);
});

test("enforces the redirect limit", async () => {
  const calls = [];
  const safeFetch = createSafeFetch({
    maxRedirects: 1,
    lookupImpl: resolver({ "one.test": ["93.184.216.34"], "two.test": ["1.1.1.1"], "three.test": ["8.8.8.8"] }),
    fetchImpl: async ({ url }) => {
      calls.push(url.hostname);
      const next = { "one.test": "two.test", "two.test": "three.test" }[url.hostname];
      return new Response(null, { status: 302, headers: { location: `https://${next}/` } });
    },
  });

  await assert.rejects(safeFetch("https://one.test/"), /redirect limit exceeded/);
  assert.deepEqual(calls, ["one.test", "two.test"]);
});

test("deduplicates bodyless HEAD checks without retaining GET bodies", async () => {
  let calls = 0;
  const safeFetch = createSafeFetch({
    lookupImpl: resolver({ "cache.test": ["93.184.216.34"] }),
    fetchImpl: async () => {
      calls += 1;
      return new Response("cached", { status: 200 });
    },
  });
  assert.equal(await (await safeFetch("https://cache.test/page")).text(), "cached");
  assert.equal(await (await safeFetch("https://cache.test/page")).text(), "cached");
  assert.equal(calls, 2);
  const head = await safeFetch("https://cache.test/page", { method: "HEAD" });
  const repeatedHead = await safeFetch("https://cache.test/page", { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(repeatedHead.status, 200);
  assert.equal(calls, 3);
});
