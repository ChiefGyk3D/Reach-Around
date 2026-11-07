// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it, vi } from "vitest";
import { guardRequest, killedResponse } from "../../src/kit/guard";

const ALLOW = ["POST", "GET"] as const;

async function snap(r: Response): Promise<string> {
  return JSON.stringify({
    status: r.status,
    headers: [...r.headers.entries()].sort(),
    body: await r.text(),
  });
}

function mk(method: string, path: string, body?: ReadableStream<Uint8Array>, init: RequestInit = {}): Request {
  const i: RequestInit & { duplex?: string } = { method, ...init };
  if (body && method !== "GET" && method !== "HEAD") {
    i.body = body;
    i.duplex = "half";
  }
  return new Request(`https://example.test${path}`, i);
}

describe("killed kit", () => {
  const paths = ["/k/gate", "/k/send", "/k/view", "/k/v/abc", "/k/anything", "/k/"];
  const methods = ["GET", "HEAD", "POST", "OPTIONS", "PUT", "DELETE", "PATCH"];

  it("answers every route and method with one identical response", async () => {
    const seen = new Set<string>();
    for (const value of ["1", "true", "TRUE", "on", "yes", "garbage"]) {
      for (const p of paths) {
        for (const m of methods) {
          const d = guardRequest(mk(m, p), { KIT_KILL: value }, { methods: ALLOW });
          expect(d.allow).toBe(false);
          if (!d.allow) seen.add(await snap(d.response));
        }
      }
    }
    expect(seen.size).toBe(1);
  });

  it("is the fixed 503 with the headers and body from the plan", async () => {
    const r = killedResponse();
    expect(r.status).toBe(503);
    expect(r.headers.get("Retry-After")).toBe("3600");
    expect(r.headers.get("Cache-Control")).toBe("private, no-store");
    expect(r.headers.get("Content-Type")).toBe("text/html; charset=utf-8");
    expect(r.headers.get("Content-Security-Policy")).toBe("default-src 'none'");
    expect(r.headers.get("Set-Cookie")).toBeNull();
    const body = await r.text();
    expect(body).toBe("<!doctype html><title>Unavailable</title><p>Temporarily unavailable.");
    expect(new TextEncoder().encode(body).length).toBe(68);
    expect(body.toLowerCase()).not.toContain("kill");
  });

  it("is checked before method and origin, and never reads the body or headers", () => {
    const pulled = vi.fn();
    const body = new ReadableStream<Uint8Array>({ pull: pulled }, { highWaterMark: 0 });
    const originCheck = vi.fn(() => true);
    const request = mk("DELETE", "/k/gate", body);
    const headerGet = vi.spyOn(request.headers, "get");
    const d = guardRequest(request, { KIT_KILL: "1" }, { methods: ALLOW, originCheck });
    expect(d.allow).toBe(false);
    expect(originCheck).not.toHaveBeenCalled();
    expect(headerGet).not.toHaveBeenCalled();
    expect(pulled).not.toHaveBeenCalled();
  });

  it("is read on every request, no module cache", () => {
    const env: { KIT_KILL?: string } = {};
    const run = () => guardRequest(mk("POST", "/k/gate"), env, { methods: ALLOW }).allow;
    expect(run()).toBe(true);
    env.KIT_KILL = "1";
    expect(run()).toBe(false);
    env.KIT_KILL = "0";
    expect(run()).toBe(true);
  });

  it("returns the same bytes for the same call", async () => {
    expect(await snap(killedResponse())).toBe(await snap(killedResponse()));
  });
});

describe("not killed", () => {
  it.each([undefined, "", "0", "false", "off", "no"])("KIT_KILL=%j passes a listed method", (v) => {
    expect(guardRequest(mk("POST", "/k/gate"), { KIT_KILL: v }, { methods: ALLOW }).allow).toBe(true);
  });
  it("refuses a method outside the allow-list with a fixed response that does not echo input", async () => {
    const a = guardRequest(mk("PURGE", "/k/gate"), {}, { methods: ALLOW });
    const b = guardRequest(mk("DELETE", "/k/other"), {}, { methods: ALLOW });
    expect(a.allow || b.allow).toBe(false);
    if (!a.allow && !b.allow) {
      expect(a.response.status).toBe(405);
      expect(a.response.headers.get("Allow")).toBe("POST, GET");
      expect(await snap(a.response)).toBe(await snap(b.response));
    }
  });
  it("matches methods case-sensitively against the normalized request method", () => {
    expect(guardRequest(mk("post", "/k/gate"), {}, { methods: ALLOW }).allow).toBe(true);
  });
  it("runs the injected origin check after the method check and refuses with a fixed 403", async () => {
    const originCheck = vi.fn(() => false);
    const d = guardRequest(mk("POST", "/k/gate"), {}, { methods: ALLOW, originCheck });
    expect(originCheck).toHaveBeenCalledTimes(1);
    expect(d.allow).toBe(false);
    if (!d.allow) {
      expect(d.response.status).toBe(403);
      expect(await d.response.text()).toBe("");
    }
    const m = vi.fn(() => true);
    guardRequest(mk("PURGE", "/k/gate"), {}, { methods: ALLOW, originCheck: m });
    expect(m).not.toHaveBeenCalled();
  });
  it("an origin check that throws refuses (fails closed)", () => {
    const d = guardRequest(mk("POST", "/k/gate"), {}, {
      methods: ALLOW,
      originCheck: () => {
        throw new Error("x");
      },
    });
    expect(d.allow).toBe(false);
  });
  it("without an origin hook, the request passes", () => {
    expect(guardRequest(mk("GET", "/k/view"), {}, { methods: ALLOW }).allow).toBe(true);
  });
  it("every refusal carries Cache-Control private, no-store", () => {
    const a = guardRequest(mk("PURGE", "/k/x"), {}, { methods: ALLOW });
    const b = guardRequest(mk("POST", "/k/x"), {}, { methods: ALLOW, originCheck: () => false });
    for (const d of [a, b]) {
      expect(d.allow).toBe(false);
      if (!d.allow) expect(d.response.headers.get("Cache-Control")).toBe("private, no-store");
    }
  });
});
