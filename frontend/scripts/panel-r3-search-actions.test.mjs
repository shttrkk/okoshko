// Isolated guard tests. Stubbed transport never qualifies as live acceptance.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  assertR3SearchRequest,
  createR3SearchClient,
  runPanelR3SearchActions,
} from "./panel-r3-search-actions.mjs";

const enabled = { OKNO_PANEL_R3_WRITES: "1" };
function transport() {
  const calls = [];
  return {
    calls,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return new Response(JSON.stringify({ fixture: true }), { status: 200 });
    },
  };
}

test("only the literal localhost:8081 HTTP origin is accepted", () => {
  for (const base of [
    "http://localhost:8080",
    "http://127.0.0.1:8081",
    "https://localhost:8081",
    "http://localhost.example:8081",
    "http://user:pass@localhost:8081",
    "http://localhost:8081/path",
    "http://localhost:8081/?x=1",
    "http://localhost:8081/#fragment",
  ]) {
    const stub = transport();
    assert.throws(() =>
      createR3SearchClient({ base, env: enabled, fetchImpl: stub.fetchImpl }),
    );
    assert.equal(stub.calls.length, 0);
  }
});

test("every non-search mutation stays forbidden even with the flag", () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    for (const path of [
      "/api/questions",
      "/api/answers/x/confirm",
      "/api/passports",
      "/api/compare",
      "/api/hotels/x/evaluate",
      "/api/hotelier/login",
      "/api/characteristics/publish",
    ]) {
      assert.throws(() =>
        assertR3SearchRequest(method, "http://localhost:8081" + path, enabled),
      );
    }
  }
  for (const suffix of ["/", "?x=1", "#fragment"])
    assert.throws(() =>
      assertR3SearchRequest(
        "POST",
        "http://localhost:8081/api/search" + suffix,
        enabled,
      ),
    );
});

test("search permission is exact and checked before transport", async () => {
  for (const value of [undefined, "0", "true", true, 1]) {
    const stub = transport();
    const client = createR3SearchClient({
      env: { OKNO_PANEL_R3_WRITES: value },
      fetchImpl: stub.fetchImpl,
    });
    await assert.rejects(client.search("центр"), /OKNO_PANEL_R3_WRITES=1/);
    assert.equal(stub.calls.length, 0);
  }
});

test("read-only API access does not require the write flag", async () => {
  const stub = transport();
  const client = createR3SearchClient({ env: {}, fetchImpl: stub.fetchImpl });
  assert.deepEqual(await client.read("/api/status"), { fixture: true });
  assert.equal(stub.calls[0].options.method, "GET");
  assert.equal(stub.calls[0].options.credentials, "omit");
  assert.equal(stub.calls[0].options.redirect, "error");
  await assert.rejects(client.read("https://example.org/api/status"));
  await assert.rejects(client.read("/api/../private"));
  assert.equal(stub.calls.length, 1);
});

test("permitted search has fixed method/path and cannot follow redirects", async () => {
  const stub = transport();
  const env = { ...enabled };
  const client = createR3SearchClient({ env, fetchImpl: stub.fetchImpl });
  await client.search("центр", { limit: 1 });
  assert.equal(stub.calls[0].url, "http://localhost:8081/api/search");
  assert.equal(stub.calls[0].options.method, "POST");
  assert.equal(stub.calls[0].options.redirect, "error");
  assert.equal(stub.calls[0].options.credentials, "omit");
  assert.deepEqual(JSON.parse(stub.calls[0].options.body), {
    text: "центр",
    limit: 1,
    compact: false,
  });
  delete env.OKNO_PANEL_R3_WRITES;
  await assert.rejects(client.search("центр"), /OKNO_PANEL_R3_WRITES=1/);
  assert.equal(
    stub.calls.length,
    1,
    "Revoking the flag prevents the next POST",
  );
});

test("runner rejects before artifacts, browser or network when disabled", async () => {
  const artifacts = join(tmpdir(), `okno-r3-search-disabled-${process.pid}`);
  assert(!existsSync(artifacts));
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    throw new Error("Network is forbidden in guard tests");
  };
  try {
    await assert.rejects(
      runPanelR3SearchActions({ env: {}, artifacts }),
      /OKNO_PANEL_R3_WRITES=1/,
    );
    assert.equal(calls, 0);
    assert(!existsSync(artifacts));
  } finally {
    globalThis.fetch = original;
  }
});

test("an API redirect is rejected after exactly one permitted request", async () => {
  let calls = 0;
  const client = createR3SearchClient({
    env: enabled,
    fetchImpl: async (_url, options) => {
      calls++;
      assert.equal(options.redirect, "error");
      return new Response(null, {
        status: 307,
        headers: { Location: "http://localhost:8081/api/passports" },
      });
    },
  });
  await assert.rejects(client.search("центр"), /HTTP 307/);
  assert.equal(calls, 1);
});
