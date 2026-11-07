// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { SITEVERIFY_URL, TURNSTILE_TIMEOUT_MS, verifyTurnstile } from "../../src/kit/turnstile";

const SECRET = "0x4AAAAAAA-secret-value-for-tests";
const HOST = "kit.example.test";
const env = { TURNSTILE_SECRET: SECRET, SITE_HOST: HOST };
const TOKEN = "XXXX.DUMMY.token-value_123";

interface Call {
  url: string;
  init: RequestInit;
  body: URLSearchParams;
}

function fake(respond: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(input), init: init ?? {}, body: new URLSearchParams(String(init?.body ?? "")) };
    calls.push(call);
    return respond(call);
  }) as unknown as typeof fetch;
  return { fn, calls };
}
const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });
const ok = (extra: Record<string, unknown> = {}) => json({ success: true, hostname: HOST, action: "gate", challenge_ts: "2026-11-06T00:00:00Z", "error-codes": [], ...extra });

afterEach(() => vi.restoreAllMocks());

describe("verifyTurnstile: accept", () => {
  it("accepts success with matching action and hostname", async () => {
    const f = fake(() => ok());
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(true);
    expect(f.calls).toHaveLength(1);
  });
  it("matches hostname case-insensitively", async () => {
    const f = fake(() => ok({ hostname: HOST.toUpperCase() }));
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(true);
  });
  it("accepts the send action for a send token", async () => {
    const f = fake(() => ok({ action: "send" }));
    expect(await verifyTurnstile(env, TOKEN, "send", f.fn)).toBe(true);
  });
  it("uses an explicit hostname option over SITE_HOST", async () => {
    const f = fake(() => ok({ hostname: "other.example.test" }));
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn, { hostname: "other.example.test" })).toBe(true);
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn, { hostname: HOST })).toBe(false);
  });
  it("skips the hostname check only when no hostname is configured at all", async () => {
    const f = fake(() => ok({ hostname: "anything.example.test" }));
    expect(await verifyTurnstile({ TURNSTILE_SECRET: SECRET }, TOKEN, "gate", f.fn)).toBe(true);
    expect(await verifyTurnstile({ TURNSTILE_SECRET: SECRET, SITE_HOST: "  " }, TOKEN, "gate", f.fn)).toBe(true);
  });
});

describe("verifyTurnstile: reject", () => {
  it("rejects success false and surfaces nothing", async () => {
    const f = fake(() => json({ success: false, "error-codes": ["invalid-input-response"] }));
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(false);
  });
  it.each(["missing-input-secret", "invalid-input-secret", "missing-input-response", "invalid-input-response", "bad-request", "timeout-or-duplicate", "internal-error"])(
    "rejects error code %s",
    async (code) => {
      const f = fake(() => json({ success: false, "error-codes": [code] }));
      expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(false);
    },
  );
  it("rejects when error-codes is present even with success true", async () => {
    const f = fake(() => ok({ "error-codes": ["internal-error"] }));
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(false);
  });
  it("rejects an action mismatch in both directions", async () => {
    expect(await verifyTurnstile(env, TOKEN, "gate", fake(() => ok({ action: "send" })).fn)).toBe(false);
    expect(await verifyTurnstile(env, TOKEN, "send", fake(() => ok({ action: "gate" })).fn)).toBe(false);
  });
  it("rejects a missing, empty or non-string action", async () => {
    for (const a of [undefined, "", null, 1, ["gate"], "GATE", "gate "]) {
      const body: Record<string, unknown> = { success: true, hostname: HOST };
      if (a !== undefined) body.action = a;
      expect(await verifyTurnstile(env, TOKEN, "gate", fake(() => json(body)).fn), JSON.stringify(a)).toBe(false);
    }
  });
  it("rejects a hostname mismatch, missing hostname or odd hostname", async () => {
    for (const h of ["evil.example.test", "kit.example.test.evil.test", "sub.kit.example.test", "", null, 1, ["kit.example.test"], undefined]) {
      const body: Record<string, unknown> = { success: true, action: "gate" };
      if (h !== undefined) body.hostname = h;
      expect(await verifyTurnstile(env, TOKEN, "gate", fake(() => json(body)).fn), JSON.stringify(h)).toBe(false);
    }
  });
  it("accepts only the boolean true as success", async () => {
    for (const s of ["true", 1, "yes", null, [true], { v: true }, undefined]) {
      const body: Record<string, unknown> = { hostname: HOST, action: "gate" };
      if (s !== undefined) body.success = s;
      expect(await verifyTurnstile(env, TOKEN, "gate", fake(() => json(body)).fn), JSON.stringify(s)).toBe(false);
    }
  });
  it("rejects non-200 statuses, including a 2xx that is not 200", async () => {
    for (const st of [201, 204, 301, 302, 400, 403, 429, 500, 503]) {
      const f = fake(() => new Response(JSON.stringify({ success: true, hostname: HOST, action: "gate" }), { status: st }));
      expect(await verifyTurnstile(env, TOKEN, "gate", f.fn), String(st)).toBe(false);
    }
  });
  it("rejects malformed JSON, non-objects and empty bodies", async () => {
    for (const raw of ["", "not json", "{", "null", "true", "[]", "[{\"success\":true}]", "\"success\"", "{\"success\":true"]) {
      const f = fake(() => new Response(raw, { status: 200 }));
      expect(await verifyTurnstile(env, TOKEN, "gate", f.fn), raw).toBe(false);
    }
  });
  it("rejects an oversized response without parsing it", async () => {
    const pad = "x".repeat(64 * 1024);
    const f = fake(() => json({ success: true, hostname: HOST, action: "gate", pad }));
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(false);
  });
  it("rejects a repeated success key that smuggles a true after a false", async () => {
    const f = fake(() => new Response(`{"success":false,"success":true,"hostname":"${HOST}","action":"gate"}`, { status: 200 }));
    // JSON.parse keeps the last duplicate; the strict parser must not let it through
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(false);
  });
});

describe("verifyTurnstile: failure closed", () => {
  it("returns false and does not throw when fetch throws", async () => {
    const f = fake(() => {
      throw new Error(`boom ${SECRET}`);
    });
    await expect(verifyTurnstile(env, TOKEN, "gate", f.fn)).resolves.toBe(false);
  });
  it("returns false when fetch rejects with a non-Error", async () => {
    const f = fake(() => Promise.reject(SECRET));
    await expect(verifyTurnstile(env, TOKEN, "gate", f.fn)).resolves.toBe(false);
  });
  it("returns false when the response body stream errors", async () => {
    const f = fake(
      () =>
        new Response(
          new ReadableStream({
            start(c) {
              c.error(new Error(SECRET));
            },
          }),
          { status: 200 },
        ),
    );
    await expect(verifyTurnstile(env, TOKEN, "gate", f.fn)).resolves.toBe(false);
  });
  it("times out through an AbortSignal and returns false", async () => {
    const f = fake(
      (call) =>
        new Promise<Response>((_res, rej) => {
          (call.init.signal as AbortSignal).addEventListener("abort", () => rej(new Error("aborted")));
        }),
    );
    const t0 = Date.now();
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 25 })).toBe(false);
    expect(Date.now() - t0).toBeLessThan(2000);
  });
  it("defaults the timeout to 3 s", async () => {
    const spy = vi.spyOn(AbortSignal, "timeout");
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn);
    expect(TURNSTILE_TIMEOUT_MS).toBe(3000);
    expect(spy).toHaveBeenCalledWith(3000);
    expect(f.calls[0]!.init.signal).toBeInstanceOf(AbortSignal);
  });
  it("fails closed on a bad secret binding without a network call", async () => {
    for (const s of [undefined, "", 42, null, {}]) {
      const f = fake(() => ok());
      expect(await verifyTurnstile({ TURNSTILE_SECRET: s as never, SITE_HOST: HOST }, TOKEN, "gate", f.fn)).toBe(false);
      expect(f.calls).toHaveLength(0);
    }
    const f = fake(() => ok());
    expect(await verifyTurnstile(undefined as never, TOKEN, "gate", f.fn)).toBe(false);
    expect(f.calls).toHaveLength(0);
  });
  it("fails closed on an unknown action without a network call", async () => {
    const f = fake(() => ok());
    expect(await verifyTurnstile(env, TOKEN, "admin" as never, f.fn)).toBe(false);
    expect(f.calls).toHaveLength(0);
  });
});

describe("verifyTurnstile: token screening", () => {
  it("makes no network call for a missing, non-string, empty or over-long token", async () => {
    for (const t of [undefined, null, 42, {}, ["a"], "", "t".repeat(2049)]) {
      const f = fake(() => ok());
      expect(await verifyTurnstile(env, t, "gate", f.fn), String(t).slice(0, 10)).toBe(false);
      expect(f.calls).toHaveLength(0);
    }
  });
  it("sends a token of exactly 2048 characters", async () => {
    const f = fake(() => ok());
    expect(await verifyTurnstile(env, "t".repeat(2048), "gate", f.fn)).toBe(true);
    expect(f.calls).toHaveLength(1);
  });
  it("makes no network call for a token with spaces or control characters", async () => {
    for (const t of ["a b", "a\r\nb", "a\u0000b", "aéb", "\ta"]) {
      const f = fake(() => ok());
      expect(await verifyTurnstile(env, t, "gate", f.fn)).toBe(false);
      expect(f.calls).toHaveLength(0);
    }
  });
});

describe("verifyTurnstile: request shape", () => {
  it("posts only secret and response to the siteverify endpoint", async () => {
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn);
    expect(SITEVERIFY_URL).toBe("https://challenges.cloudflare.com/turnstile/v0/siteverify");
    const c = f.calls[0]!;
    expect(c.url).toBe(SITEVERIFY_URL);
    expect(c.init.method).toBe("POST");
    expect([...c.body.keys()].sort()).toEqual(["response", "secret"]);
    expect(c.body.get("secret")).toBe(SECRET);
    expect(c.body.get("response")).toBe(TOKEN);
  });
  it("never sends remoteip, in the body, the URL or the headers", async () => {
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn, { idempotencyKey: "123e4567-e89b-12d3-a456-426614174000" });
    const c = f.calls[0]!;
    expect(c.body.has("remoteip")).toBe(false);
    expect(c.url).not.toMatch(/remoteip|\?/i);
    const h = JSON.stringify([...new Headers(c.init.headers).entries()]).toLowerCase();
    expect(h).not.toMatch(/remoteip|connecting-ip|forwarded|real-ip/);
    expect(String(c.init.body)).not.toMatch(/remoteip/i);
  });
  it("adds an idempotency key only when given a UUID, and refuses anything else", async () => {
    const uuid = "123e4567-e89b-12d3-a456-426614174000";
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn, { idempotencyKey: uuid });
    expect(f.calls[0]!.body.get("idempotency_key")).toBe(uuid);
    const g = fake(() => ok());
    expect(await verifyTurnstile(env, TOKEN, "gate", g.fn, { idempotencyKey: "not a uuid\r\n" })).toBe(false);
    expect(g.calls).toHaveLength(0);
  });
  it("does not follow redirects", async () => {
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn);
    expect(f.calls[0]!.init.redirect).toBe("error");
  });
  it("sends no cookies or credentials", async () => {
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn);
    expect("credentials" in f.calls[0]!.init).toBe(false);
  });
});

describe("verifyTurnstile: secret handling", () => {
  it("never writes the secret or token to any console method", async () => {
    const methods = ["log", "info", "warn", "error", "debug", "trace"] as const;
    const spies = methods.map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    const scenarios: Array<() => Response | Promise<Response>> = [
      () => ok(),
      () => json({ success: false, "error-codes": ["invalid-input-secret"] }),
      () => {
        throw new Error(`fail ${SECRET} ${TOKEN}`);
      },
      () => new Response(`garbage ${SECRET}`, { status: 200 }),
      () => new Response(SECRET, { status: 500 }),
    ];
    for (const s of scenarios) await verifyTurnstile(env, TOKEN, "gate", fake(s).fn);
    for (const spy of spies) {
      const text = JSON.stringify(spy.mock.calls);
      expect(text).not.toContain(SECRET);
      expect(text).not.toContain(TOKEN);
    }
  });
  it("never rejects, so no error object can carry the secret out", async () => {
    const f = fake(() => {
      throw new Error(SECRET);
    });
    let thrown: unknown = null;
    try {
      await verifyTurnstile(env, TOKEN, "gate", f.fn);
    } catch (e) {
      thrown = e;
    }
    expect(thrown).toBeNull();
  });
});
