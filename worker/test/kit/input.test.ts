// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import vectors from "../../../tests/fixtures/email_vectors.json";
import { FIELD_EMAIL, FIELD_HONEYPOT, FIELD_TURNSTILE, MAX_FORM_BYTES, normalizeEmail, readForm } from "../../src/kit/input";

const CT = "application/x-www-form-urlencoded";

function post(body: string | Uint8Array | null, headers: Record<string, string> = { "content-type": CT }): Request {
  return new Request("https://kit.example.test/gate", { method: "POST", headers, body });
}
const enc = (s: string) => encodeURIComponent(s);

describe("normalizeEmail: shared vectors", () => {
  it("collects a meaningful number of vectors", () => {
    expect(vectors.valid.length).toBeGreaterThanOrEqual(5);
    expect(vectors.invalid.length).toBeGreaterThanOrEqual(40);
  });
  it.each(vectors.valid)("accepts %j as %j", (input, normalized) => {
    expect(normalizeEmail(input)).toBe(normalized);
  });
  it.each(vectors.invalid.map((v) => [JSON.stringify(v), v]))("rejects %s", (_label, input) => {
    expect(normalizeEmail(input)).toBeNull();
  });
});

describe("normalizeEmail: bounds and types", () => {
  const dom = "example.com";
  it("accepts a 64 character local part and rejects 65", () => {
    expect(normalizeEmail("a".repeat(64) + "@" + dom)).toBe("a".repeat(64) + "@" + dom);
    expect(normalizeEmail("a".repeat(65) + "@" + dom)).toBeNull();
  });
  it("accepts exactly 254 characters and rejects 255", () => {
    const tail = "." + "d".repeat(63) + "." + "e".repeat(63) + ".com";
    const ok = "l".repeat(64) + "@" + "c".repeat(63) + tail;
    // 64 + 1 + 63 + 1+63 + 1+63 + 4 = 260: trim the first label so the total is 254
    const addr254 = ok.slice(0, 254 - tail.length) + tail;
    expect(addr254.length).toBe(254);
    expect(normalizeEmail(addr254)).toBe(addr254);
    const addr255 = "x" + addr254;
    expect(addr255.length).toBe(255);
    expect(normalizeEmail(addr255)).toBeNull();
  });
  it("rejects a 64 character domain label and a 255 character address", () => {
    expect(normalizeEmail("a@" + "b".repeat(64) + ".com")).toBeNull();
    expect(normalizeEmail("a".repeat(251) + "@b.co")).toBeNull();
  });
  it("rejects an address over 254 characters even when each part is legal", () => {
    const d = ("b".repeat(60) + ".").repeat(5) + "com";
    expect(normalizeEmail("a@" + d)).toBeNull();
  });
  it("rejects non-strings", () => {
    for (const v of [undefined, null, 42, true, ["a@b.com", "c@d.com"], ["a@b.com"], {}, { toString: () => "a@b.com" }, Symbol("x")]) {
      expect(normalizeEmail(v)).toBeNull();
    }
  });
  it("rejects every ASCII control character and every space-like character", () => {
    for (let c = 0; c < 0x21; c++) {
      const ch = String.fromCharCode(c);
      expect(normalizeEmail(`a${ch}b@example.com`), `0x${c.toString(16)} in local`).toBeNull();
      expect(normalizeEmail(`a@example${ch}.com`), `0x${c.toString(16)} in domain`).toBeNull();
      expect(normalizeEmail(`a@example.com${ch}`), `0x${c.toString(16)} trailing`).toBeNull();
    }
    expect(normalizeEmail("a\u007f@example.com")).toBeNull();
  });
  it("rejects any non-ASCII character, including ones that lowercase to ASCII", () => {
    for (const cp of [0x80, 0xa0, 0xe9, 0x212a, 0x2028, 0x200b, 0x200e, 0x202e, 0x2066, 0xfeff, 0xff21, 0x0430, 0x1f600]) {
      const ch = String.fromCodePoint(cp);
      expect(normalizeEmail(`a${ch}@example.com`), `U+${cp.toString(16)} local`).toBeNull();
      expect(normalizeEmail(`a@ex${ch}ample.com`), `U+${cp.toString(16)} domain`).toBeNull();
    }
  });
  it("lowercases only after the ASCII check", () => {
    expect(normalizeEmail("MiXeD.Case@EXAMPLE.Org")).toBe("mixed.case@example.org");
  });
  it("does not strip or rewrite plus addressing", () => {
    expect(normalizeEmail("User+News@Example.com")).toBe("user+news@example.com");
  });
});

describe("readForm", () => {
  it("returns the normalized email and the token", async () => {
    const r = await readForm(post(`${FIELD_EMAIL}=${enc("Partner@Example.COM")}&${FIELD_TURNSTILE}=tok-1_2.3`));
    expect(r).toEqual({ email: "partner@example.com", turnstile: "tok-1_2.3" });
  });
  it("decodes plus and percent escapes before validating", async () => {
    const r = await readForm(post(`${FIELD_EMAIL}=a%2Btag%40example.com&${FIELD_TURNSTILE}=t`));
    expect(r?.email).toBe("a+tag@example.com");
  });
  it("reports an invalid email as null without rejecting the form", async () => {
    const r = await readForm(post(`${FIELD_EMAIL}=${enc("a@b.com\r\nBcc: x@y.com")}&${FIELD_TURNSTILE}=t`));
    expect(r).toEqual({ email: null, turnstile: "t" });
  });
  it("treats a percent-encoded space or NUL in the email as invalid", async () => {
    for (const bad of ["%20a%40b.com", "a%40b.com%20", "a%00%40b.com", "a%0d%0a%40b.com"]) {
      const r = await readForm(post(`${FIELD_EMAIL}=${bad}&${FIELD_TURNSTILE}=t`));
      expect(r?.email, bad).toBeNull();
    }
  });
  it("returns nulls for a missing email or token", async () => {
    expect(await readForm(post(""))).toEqual({ email: null, turnstile: null });
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com`))).toEqual({ email: "a@b.com", turnstile: null });
  });
  it("accepts an empty honeypot and rejects a filled one", async () => {
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&${FIELD_TURNSTILE}=t&${FIELD_HONEYPOT}=`))).not.toBeNull();
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&${FIELD_TURNSTILE}=t&${FIELD_HONEYPOT}=x`))).toBeNull();
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&${FIELD_TURNSTILE}=t&${FIELD_HONEYPOT}=%20`))).toBeNull();
  });
  it("rejects a repeated key, even with identical values", async () => {
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&${FIELD_EMAIL}=c%40d.com&${FIELD_TURNSTILE}=t`))).toBeNull();
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&${FIELD_EMAIL}=a%40b.com`))).toBeNull();
    expect(await readForm(post(`${FIELD_TURNSTILE}=t&${FIELD_TURNSTILE}=u`))).toBeNull();
    expect(await readForm(post(`${FIELD_HONEYPOT}=&${FIELD_HONEYPOT}=`))).toBeNull();
  });
  it("rejects an unknown key", async () => {
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&bcc=x%40y.com`))).toBeNull();
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&=x`))).toBeNull();
    expect(await readForm(post(`${FIELD_EMAIL.toUpperCase()}=a%40b.com`))).toBeNull();
  });
  it("rejects a repeated key that differs only by encoding", async () => {
    expect(await readForm(post(`${FIELD_EMAIL}=a%40b.com&em%61il=c%40d.com`))).toBeNull();
  });
  it("requires the content type exactly", async () => {
    const body = `${FIELD_EMAIL}=a%40b.com`;
    for (const ct of [
      "application/json",
      "text/plain",
      "multipart/form-data; boundary=x",
      `${CT}; charset=utf-8`,
      `${CT};`,
      CT.toUpperCase(),
    ]) {
      expect(await readForm(post(body, { "content-type": ct })), ct).toBeNull();
    }
    expect(await readForm(new Request("https://kit.example.test/gate", { method: "POST", body: new Uint8Array(0) }))).toBeNull();
  });
  it("rejects a JSON body", async () => {
    expect(await readForm(post(JSON.stringify({ email: "a@b.com" }), { "content-type": "application/json" }))).toBeNull();
  });
  it("accepts a body of exactly the cap and rejects one byte more", async () => {
    const head = `${FIELD_TURNSTILE}=`;
    const at = head + "t".repeat(MAX_FORM_BYTES - head.length);
    expect(at.length).toBe(MAX_FORM_BYTES);
    const over = at + "t";
    // the token field has its own cap, so a body at the form cap is still a valid form with a null token
    expect(await readForm(post(at))).toEqual({ email: null, turnstile: null });
    expect(await readForm(post(over))).toBeNull();
  });
  it("rejects a 5 KiB body whether or not content-length is present", async () => {
    const big = `${FIELD_EMAIL}=a%40b.com&${FIELD_TURNSTILE}=${"t".repeat(5 * 1024)}`;
    expect(await readForm(post(big))).toBeNull();
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        const bytes = new TextEncoder().encode(big);
        c.enqueue(bytes.slice(0, 3000));
        c.enqueue(bytes.slice(3000));
        c.close();
      },
    });
    const req = new Request("https://kit.example.test/gate", {
      method: "POST",
      headers: { "content-type": CT },
      body: stream,
      duplex: "half",
    } as RequestInit);
    expect(await readForm(req)).toBeNull();
  });
  it("stops reading a stream once the cap is passed", async () => {
    let pulled = 0;
    const chunk = new TextEncoder().encode("a".repeat(1024));
    const stream = new ReadableStream<Uint8Array>({
      pull(c) {
        pulled++;
        if (pulled > 200) c.close();
        else c.enqueue(chunk);
      },
    });
    const req = new Request("https://kit.example.test/gate", {
      method: "POST",
      headers: { "content-type": CT },
      body: stream,
      duplex: "half",
    } as RequestInit);
    expect(await readForm(req)).toBeNull();
    expect(pulled).toBeLessThan(20);
  });
  it("rejects malformed percent escapes in any key or value, accepts valid ones", async () => {
    for (const body of [
      `${FIELD_TURNSTILE}=%`,
      `${FIELD_TURNSTILE}=%ZZ`,
      `${FIELD_TURNSTILE}=%4`,
      `${FIELD_TURNSTILE}=abc%`,
      `${FIELD_TURNSTILE}=%4Z`,
      `${FIELD_EMAIL}=a%40b.com&${FIELD_TURNSTILE}=%g1`,
      `${FIELD_EMAIL}%=a%40b.com`,
      `%=x`,
    ]) expect(await readForm(post(body)), body).toBeNull();
    expect((await readForm(post(`${FIELD_TURNSTILE}=%41`)))?.turnstile).toBe("A");
    expect((await readForm(post(`${FIELD_TURNSTILE}=%4a%4A`)))?.turnstile).toBe("JJ");
  });
  describe("hostile streams", () => {
    function streamReq(src: UnderlyingSource<Uint8Array>): Request {
      return new Request("https://kit.example.test/gate", {
        method: "POST",
        headers: { "content-type": CT },
        body: new ReadableStream<Uint8Array>(src),
        duplex: "half",
      } as RequestInit);
    }
    it("refuses an unending stream of empty chunks without pulling forever", async () => {
      let pulled = 0;
      const req = streamReq({
        pull(c) {
          pulled++;
          c.enqueue(new Uint8Array(0));
        },
      });
      expect(await readForm(req)).toBeNull();
      expect(pulled).toBeLessThan(100);
    });
    it("lets a few empty chunks between real ones through", async () => {
      const parts = [new Uint8Array(0), new TextEncoder().encode("email=a%40"), new Uint8Array(0), new Uint8Array(0), new TextEncoder().encode("b.com")];
      const req = streamReq({
        pull(c) {
          const p = parts.shift();
          if (p === undefined) c.close();
          else c.enqueue(p);
        },
      });
      expect(await readForm(req)).toEqual({ email: "a@b.com", turnstile: null });
    });
    it("refuses a flood of one-byte chunks", async () => {
      let pulled = 0;
      const req = streamReq({
        pull(c) {
          pulled++;
          c.enqueue(new Uint8Array([0x61]));
        },
      });
      expect(await readForm(req)).toBeNull();
      expect(pulled).toBeLessThanOrEqual(MAX_FORM_BYTES + 8);
    });
    it("returns null at the deadline when a pull never settles", async () => {
      const req = streamReq({ pull: () => new Promise<void>(() => undefined) });
      const t0 = Date.now();
      expect(await readForm(req, { deadlineMs: 50 })).toBeNull();
      expect(Date.now() - t0).toBeLessThan(2000);
    });
    it("does not wait on a cancel that never settles", async () => {
      const req = streamReq({
        pull: () => new Promise<void>(() => undefined),
        cancel: () => new Promise<void>(() => undefined),
      });
      const t0 = Date.now();
      expect(await readForm(req, { deadlineMs: 50 })).toBeNull();
      expect(Date.now() - t0).toBeLessThan(2000);
    });
    it("does not wait on a cancel that never settles after the cap is passed", async () => {
      const chunk = new TextEncoder().encode("a".repeat(2048));
      const req = streamReq({
        pull(c) {
          c.enqueue(chunk);
        },
        cancel: () => new Promise<void>(() => undefined),
      });
      const t0 = Date.now();
      expect(await readForm(req)).toBeNull();
      expect(Date.now() - t0).toBeLessThan(2000);
    });
  });
  it("rejects invalid UTF-8 and over-long or odd tokens as null", async () => {
    expect(await readForm(post(new Uint8Array([0x65, 0x6d, 0x61, 0x69, 0x6c, 0x3d, 0xff, 0xfe])))).toBeNull();
    for (const t of ["", "a b", "a\nb", "a\u0000b", "t".repeat(2049), "é".normalize()]) {
      const r = await readForm(post(`${FIELD_TURNSTILE}=${enc(t)}`));
      expect(r?.turnstile, JSON.stringify(t.slice(0, 8))).toBeNull();
    }
    const r = await readForm(post(`${FIELD_TURNSTILE}=${"t".repeat(2048)}`));
    expect(r?.turnstile).toBe("t".repeat(2048));
  });
  it("does not parse a GET query string", async () => {
    const req = new Request(`https://kit.example.test/gate?${FIELD_EMAIL}=a%40b.com`, { method: "GET" });
    expect(await readForm(req)).toBeNull();
  });
});
