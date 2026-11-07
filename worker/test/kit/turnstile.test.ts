// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it, vi } from "vitest";
import { SITEVERIFY_URL, TURNSTILE_TIMEOUT_MS, expectedHostname, parseStrictJson, verifyTurnstile } from "../../src/kit/turnstile";

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
  it("fails closed, with no network call, when no expected hostname is configured", async () => {
    for (const e of [{ TURNSTILE_SECRET: SECRET }, { TURNSTILE_SECRET: SECRET, SITE_HOST: "" }, { TURNSTILE_SECRET: SECRET, SITE_HOST: "  " }]) {
      const f = fake(() => ok({ hostname: "anything.example.test" }));
      expect(await verifyTurnstile(e, TOKEN, "gate", f.fn)).toBe(false);
      expect(await verifyTurnstile(e, TOKEN, "gate", f.fn, { hostname: " " })).toBe(false);
      expect(f.calls).toHaveLength(0);
    }
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
  const raw = (s: string) => fake(() => new Response(s, { status: 200 })).fn;
  it("rejects a repeated decision key, however it is spelled", async () => {
    const good = `"hostname":"${HOST}","action":"gate"`;
    const bodies = [
      `{"success":false,"success":true,${good}}`,
      `{"success":false,"\\u0073uccess":true,${good}}`,
      `{"success":false,"succ\\u0065ss":true,${good}}`,
      `{"success":true,"hostname":"evil.example.test","\\u0068ostname":"${HOST}","action":"gate"}`,
      `{"success":true,"hostname":"${HOST}","action":"send","\\u0061ction":"gate"}`,
      `{"success":true,${good},"error-codes":["x"],"error\\u002dcodes":[]}`,
      `{"success":true,${good},"error-codes":["x"],"error-codes":[]}`,
    ];
    for (const b of bodies) expect(await verifyTurnstile(env, TOKEN, "gate", raw(b)), b).toBe(false);
  });
  it("rejects a duplicate key at any nesting level", async () => {
    const good = `"success":true,"hostname":"${HOST}","action":"gate"`;
    for (const b of [
      `{${good},"metadata":{"a":1,"a":2}}`,
      `{${good},"metadata":{"a":{"b":1,"\\u0062":2}}}`,
      `{${good},"list":[{"k":1,"k":2}]}`,
    ]) expect(await verifyTurnstile(env, TOKEN, "gate", raw(b)), b).toBe(false);
  });
  it("compares decoded keys, including surrogate pairs", async () => {
    const good = `"success":true,"hostname":"${HOST}","action":"gate"`;
    expect(await verifyTurnstile(env, TOKEN, "gate", raw(`{${good},"\\ud83d\\ude00":1,"\u{1F600}":2}`))).toBe(false);
    expect(await verifyTurnstile(env, TOKEN, "gate", raw(`{${good},"\\ud83d\\ude00":1,"\\uD83D\\uDE00":2}`))).toBe(false);
    expect(await verifyTurnstile(env, TOKEN, "gate", raw(`{${good},"\\ud83d\\ude00":1,"\\ud83d\\ude01":2}`))).toBe(true);
  });
  it("accepts an escaped spelling of a key when it appears once", async () => {
    expect(await verifyTurnstile(env, TOKEN, "gate", raw(`{"\\u0073uccess":true,"hostname":"${HOST}","action":"gate"}`))).toBe(true);
  });
  it("rejects malformed JSON and trailing content", async () => {
    const good = `"success":true,"hostname":"${HOST}","action":"gate"`;
    for (const b of [
      `{${good}} x`,
      `{${good}}{}`,
      `{${good},}`,
      `{${good}\n`,
      `{'success':true}`,
      `{${good},"n":01}`,
      `{${good},"n":1.}`,
      `{${good},"n":+1}`,
      `{${good},"s":"a\tb"}`,
      `{${good},"s":"\\x41"}`,
      `{${good},"s":"\\u12"}`,
      `{${good},"s":"\\ud83"}`,
      `{${good},"v":NaN}`,
      `{${good},"v":undefined}`,
      ` {${good}}`,
      `{${good}}//c`,
      `{${good},"a":[1,]}`,
      `{${good},"a":${"[".repeat(200)}${"]".repeat(200)}}`,
    ]) expect(await verifyTurnstile(env, TOKEN, "gate", raw(b)), JSON.stringify(b)).toBe(false);
  });
  it("accepts ordinary whitespace and nested values", async () => {
    const b = ` \n{ "success" : true , "hostname":"${HOST}", "action":"gate", "metadata":{"n":[1,-2.5e3,null,true,"x"]} }\r\n`;
    expect(await verifyTurnstile(env, TOKEN, "gate", raw(b))).toBe(true);
  });
  it("does not let a prototype key through or pollute the runtime", async () => {
    for (const b of [
      `{"__proto__":{"success":true,"hostname":"${HOST}","action":"gate"}}`,
      `{"__proto__":{"success":true},"hostname":"${HOST}","action":"gate"}`,
      `{"\\u005f_proto__":{"success":true},"hostname":"${HOST}","action":"gate"}`,
      `{"constructor":{"prototype":{"success":true}},"hostname":"${HOST}","action":"gate"}`,
    ]) expect(await verifyTurnstile(env, TOKEN, "gate", raw(b)), b).toBe(false);
    expect(({} as Record<string, unknown>).success).toBeUndefined();
    expect(Object.prototype.hasOwnProperty.call(Object.prototype, "success")).toBe(false);
  });
});

describe("parseStrictJson", () => {
  it("builds objects without a prototype", () => {
    const v = parseStrictJson('{"a":{"b":1}}') as Record<string, Record<string, unknown>>;
    expect(Object.getPrototypeOf(v)).toBeNull();
    expect(Object.getPrototypeOf(v.a)).toBeNull();
  });
  it("rejects lone surrogates in values and keys, accepts valid pairs", () => {
    for (const s of [
      '{"k":"\\ud800"}',
      '{"k":"\\udc00"}',
      '{"k":"\\ud800x"}',
      '{"k":"\\udc00\\ud800"}',
      '{"k":"\\ud800\\ud800"}',
      '{"k":"x\\udbff"}',
      '{"\\ud800":1}',
      '{"\\udfff":1}',
      '{"a":["\\ud83d"]}',
    ]) expect(() => parseStrictJson(s), s).toThrow();
    expect(parseStrictJson('{"k":"\\ud83d\\ude00"}')).toBeTruthy();
    expect(parseStrictJson('{"\\ud83d\\ude00":1}')).toBeTruthy();
  });
  it("throws on a prototype key, plain or escaped", () => {
    expect(() => parseStrictJson('{"__proto__":{}}')).toThrow();
    expect(() => parseStrictJson('{"\\u005f_proto__":{}}')).toThrow();
  });
  it("throws on a duplicate decoded key", () => {
    expect(() => parseStrictJson('{"a":1,"\\u0061":2}')).toThrow();
  });
});

describe("expectedHostname", () => {
  it("prefers the option, then SITE_HOST, lowercased and trimmed", () => {
    expect(expectedHostname({ SITE_HOST: " Kit.Example.TEST " }, {})).toBe("kit.example.test");
    expect(expectedHostname({ SITE_HOST: "a.example.test" }, { hostname: "B.example.test" })).toBe("b.example.test");
  });
  it("accepts label edges that are legal", () => {
    expect(expectedHostname({ SITE_HOST: "a".repeat(63) + ".example.test" }, {})).toBe("a".repeat(63) + ".example.test");
    expect(expectedHostname({ SITE_HOST: "a-b.x1.example" }, {})).toBe("a-b.x1.example");
    expect(expectedHostname({ SITE_HOST: "xn--bcher-kva.example" }, {})).toBe("xn--bcher-kva.example");
  });
  it("returns null when nothing usable is configured", () => {
    for (const [e, o] of [
      [{}, {}],
      [{ SITE_HOST: "" }, {}],
      [{ SITE_HOST: "   " }, {}],
      [{ SITE_HOST: 5 }, {}],
      [{ SITE_HOST: "a.example.test" }, { hostname: "" }],
      [{ SITE_HOST: "a.example.test" }, { hostname: "  " }],
      [{ SITE_HOST: "a.example.test" }, { hostname: 7 }],
      [{ SITE_HOST: "bad host.example.test" }, {}],
      [{ SITE_HOST: "a..example.test" }, {}],
      [{ SITE_HOST: ".example.test" }, {}],
      [{ SITE_HOST: "example.test." }, {}],
      [{ SITE_HOST: "a.-bad.example" }, {}],
      [{ SITE_HOST: "bad-.example" }, {}],
      [{ SITE_HOST: "-bad.example" }, {}],
      [{ SITE_HOST: "a_b.example" }, {}],
      [{ SITE_HOST: "a".repeat(64) + ".example" }, {}],
      [{ SITE_HOST: "localhost" }, {}],
      [{ SITE_HOST: ("a".repeat(60) + ".").repeat(5) + "com" }, {}],
      [{ SITE_HOST: "a.example.test/path" }, {}],
      [{ SITE_HOST: "a.example.test:8443" }, {}],
      [{ SITE_HOST: "é.example.test" }, {}],
      [undefined, {}],
    ] as Array<[never, never]>) expect(expectedHostname(e, o)).toBeNull();
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
    await verifyTurnstile(env, TOKEN, "gate", f.fn);
    const c = f.calls[0]!;
    expect(c.body.has("remoteip")).toBe(false);
    expect(c.url).not.toMatch(/remoteip|\?/i);
    const h = JSON.stringify([...new Headers(c.init.headers).entries()]).toLowerCase();
    expect(h).not.toMatch(/remoteip|connecting-ip|forwarded|real-ip/);
    expect(String(c.init.body)).not.toMatch(/remoteip/i);
  });
  it("offers no idempotency option and sends exactly two fields", async () => {
    const f = fake(() => ok());
    await verifyTurnstile(env, TOKEN, "gate", f.fn, { idempotencyKey: "123e4567-e89b-12d3-a456-426614174000" } as never);
    expect([...f.calls[0]!.body.keys()].sort()).toEqual(["response", "secret"]);
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

describe("verifyTurnstile: bounded response read", () => {
  const stream = (src: UnderlyingSource<Uint8Array>) => new Response(new ReadableStream<Uint8Array>(src), { status: 200 });
  const never = () => new Promise<void>(() => undefined);
  const settles = async (p: Promise<boolean>, ms = 2000) => Promise.race([p, new Promise<"hung">((r) => setTimeout(() => r("hung"), ms))]);

  it("returns false when the body never produces data", async () => {
    const f = fake(() => stream({ pull: never }));
    expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 30 }))).toBe(false);
  });
  it("returns false when the body is over the cap and cancel never settles", async () => {
    const f = fake(() =>
      stream({
        start(c) {
          c.enqueue(new Uint8Array(16 * 1024 + 1));
        },
        cancel: never,
      }),
    );
    expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 30 }))).toBe(false);
  });
  it("returns false when the body dribbles bytes past the deadline", async () => {
    const text = new TextEncoder().encode(`{"success":true,"hostname":"${HOST}","action":"gate"}`);
    let n = 0;
    const f = fake(() =>
      stream({
        async pull(c) {
          await new Promise((r) => setTimeout(r, 8));
          if (n >= text.length) c.close();
          else c.enqueue(text.subarray(n, ++n));
        },
      }),
    );
    expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 60 }))).toBe(false);
  });
  it("returns false for an unending stream of empty chunks", async () => {
    const f = fake(() => stream({ pull: (c) => c.enqueue(new Uint8Array(0)) }));
    expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn))).toBe(false);
  });
  it("returns false for a long run of tiny chunks", async () => {
    const text = new TextEncoder().encode(`{"success":true,"hostname":"${HOST}","action":"gate","pad":"${"x".repeat(400)}"}`);
    let n = 0;
    const f = fake(() =>
      stream({
        pull(c) {
          if (n >= text.length) c.close();
          else c.enqueue(text.subarray(n, ++n));
        },
      }),
    );
    expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn))).toBe(false);
    expect(n).toBeLessThan(text.length);
  });
  it("still accepts a normal answer delivered in a few chunks", async () => {
    const text = new TextEncoder().encode(`{"success":true,"hostname":"${HOST}","action":"gate"}`);
    const parts = [text.subarray(0, 10), text.subarray(10, 30), text.subarray(30)];
    const f = fake(() =>
      stream({
        pull(c) {
          const p = parts.shift();
          if (p === undefined) c.close();
          else c.enqueue(p);
        },
      }),
    );
    expect(await verifyTurnstile(env, TOKEN, "gate", f.fn)).toBe(true);
  });
  it("releases the reader lock after success, refusal, oversize and stall", async () => {
    const cases: Array<[() => Response, boolean]> = [
      [() => ok(), true],
      [() => json({ success: false }), false],
      [
        () =>
          stream({
            start(c) {
              c.enqueue(new Uint8Array(16 * 1024 + 1));
            },
            cancel: never,
          }),
        false,
      ],
      [() => stream({ pull: never, cancel: never }), false],
    ];
    for (const [mk, want] of cases) {
      const res = mk();
      const f = fake(() => res);
      expect(await settles(verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 30 }))).toBe(want);
      await new Promise((r) => setTimeout(r, 20));
      expect(res.body!.locked).toBe(false);
    }
  });
  it("shares one deadline between the fetch and the body read", async () => {
    vi.useFakeTimers();
    try {
      const f = fake(() => new Promise<Response>((r) => setTimeout(() => r(stream({ pull: never })), 50)));
      let settled: boolean | null = null;
      const p = verifyTurnstile(env, TOKEN, "gate", f.fn, { timeoutMs: 100 }).then((v) => (settled = v));
      await vi.advanceTimersByTimeAsync(50);
      expect(settled).toBeNull();
      await vi.advanceTimersByTimeAsync(49);
      expect(settled).toBeNull();
      await vi.advanceTimersByTimeAsync(3);
      expect(await p).toBe(false);
      expect(settled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("expectedHostname: A-labels", () => {
  it("rejects invalid or non round-tripping xn-- labels, accepts valid ones", () => {
    for (const h of ["xn--a.example.test", "xn--.example.test", "a.xn--", "xn--abc-.example.test", "ab--cd.example.test", "xn--bcher-kva9.example.test"]) {
      expect(expectedHostname({ SITE_HOST: h }, {}), h).toBeNull();
    }
    expect(expectedHostname({ SITE_HOST: "xn--bcher-kva.example.test" }, {})).toBe("xn--bcher-kva.example.test");
  });
});
