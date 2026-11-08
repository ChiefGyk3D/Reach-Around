// SPDX-License-Identifier: Apache-2.0
// Dump test (issue #272). Two independent checks, neither a copy of the
// scripts/check-dump.py patterns:
//
//  1. SCHEMA. Every table and column in the real database must be in the
//     allowlist scripts/dump-schema.json (the same file the dump checker
//     reads). A renamed or added column fails, whatever it is called.
//  2. BIND VALUES. The database handed to the click route, the view route and
//     the rollup records every value bound to every statement. Request data
//     (addresses, a user agent, a header value) is planted in synthetic form;
//     no persisted value may contain the planted text or a transformed form of
//     it (upper/lower case, hex, base64, percent-encoding, an integer address,
//     any 6-character fragment, which also covers truncated and split storage).
//     The final database is searched for the same forms.
import { env as testEnv } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";
import allow from "../../scripts/dump-schema.json";
import { handleClick, type Env as ClickEnv } from "../src/click";
import { rollupClosedDays } from "../src/viewrollup";
import { handleView } from "../src/view";
import { NOW, beacon, clearAll, fakeLimiter, makeEnv } from "./views/helpers";

const DB = testEnv.DB;

// ---- the planted request data (synthetic; documentation ranges) -------------
const PROBE_V4 = "203.0.113.211";
const PROBE_V6 = "2001:db8:abcd::42";
const PROBE_UA = "ZzProbeAgent/9.9 (Quux; Fnord 42)";
const PROBE_HDR = "hdr-probe-QX7731";
const PROBES = [PROBE_V4, PROBE_V6, PROBE_UA, PROBE_HDR];
// Realistic strings that must also be absent whole (their short fragments are
// ordinary words such as "chrome", so only the full text is searched).
const REALISTIC = [
  "198.51.100.7",
  "2001:db8::7",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.84 Safari/537.36",
  "curl/8.5.0",
];
const WINDOW = 6;

const utf8hex = (s: string) =>
  [...new TextEncoder().encode(s)].map((b) => b.toString(16).padStart(2, "0")).join("");
const b64 = (s: string) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const b64url = (s: string) => b64(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

function v4Int(ip: string): number | null {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(ip);
  if (!m) return null;
  return ((+m[1]! * 256 + +m[2]!) * 256 + +m[3]!) * 256 + +m[4]!;
}

/** Percent-encode every byte that is not a letter or digit (encodeURIComponent leaves . - _ ~ ! * ' ( ) alone). */
function pctAll(s: string): string[] {
  const enc = [...new TextEncoder().encode(s)].map((b) =>
    /[A-Za-z0-9]/.test(String.fromCharCode(b)) ? String.fromCharCode(b) : "%" + b.toString(16).toUpperCase().padStart(2, "0"),
  );
  const upper = enc.join("");
  return [upper, upper.toLowerCase(), upper.replace(/%/g, "%25")];
}

/** Every spelling of an IPv4 address a resolver reads: dotted, legacy octal, hex components, 1 to 3 component shortenings. */
function v4Spellings(ip: string): string[] {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(ip);
  if (!m) return [];
  const o = m.slice(1).map(Number) as [number, number, number, number];
  const n = v4Int(ip)!;
  const comp = (xs: number[], f: (x: number) => string) => xs.map(f).join(".");
  const oct = (x: number) => "0" + x.toString(8);
  const hex = (x: number) => "0x" + x.toString(16);
  return [
    comp(o, String),
    comp(o, (x) => String(x).padStart(3, "0")),
    comp(o, oct),
    comp(o, (x) => (x === 0 ? "0" : oct(x))),
    comp(o, hex),
    comp(o, (x) => "0x" + x.toString(16).padStart(2, "0")),
    comp(o, (x) => x.toString(16)), // not a valid form, but a split-hex copy
    `${o[0]}.${o[1]}.${o[2] * 256 + o[3]}`,
    `${o[0]}.${o[1] * 65536 + o[2] * 256 + o[3]}`,
    `0${o[0].toString(8)}.0${(o[1] * 65536 + o[2] * 256 + o[3]).toString(8)}`,
    `${hex(o[0])}.${hex(o[1] * 65536 + o[2] * 256 + o[3])}`,
    String(n),
    "0" + n.toString(8),
    "0x" + n.toString(16).padStart(8, "0"),
    n.toString(16).padStart(8, "0"),
    "::ffff:" + ip,
    "::ffff:" + (n >>> 16).toString(16) + ":" + (n & 0xffff).toString(16),
  ];
}

/** Every spelling of an IPv6 address: compressed, fully expanded, unseparated, bracketed, with a zone. */
function v6Spellings(ip: string): string[] {
  if (!ip.includes(":") || ip.includes(".")) return [];
  const [head = "", tail = ""] = ip.split("::");
  const h = head ? head.split(":") : [];
  const t = tail ? tail.split(":") : [];
  const groups = ip.includes("::") ? [...h, ...Array(8 - h.length - t.length).fill("0"), ...t] : h;
  const full = groups.map((g) => g.padStart(4, "0"));
  return [full.join(":"), full.join(""), `[${ip}]`, `${ip}%eth0`, `${ip}%25eth0`, ip.toUpperCase()];
}

/** Every transformed form of a planted string, lower-cased for comparison. */
function forms(s: string): string[] {
  const out = new Set<string>([
    s,
    s.toUpperCase(),
    utf8hex(s),
    b64(s),
    b64url(s),
    encodeURIComponent(s),
    ...pctAll(s),
    s.split("").reverse().join(""),
    ...v4Spellings(s),
    ...v6Spellings(s),
  ]);
  for (const sp of [...v4Spellings(s), ...v6Spellings(s)]) for (const p of pctAll(sp)) out.add(p);
  return [...out].map((f) => f.toLowerCase());
}

/** Every WINDOW-long fragment of every form (a truncated or split copy shares one). */
function fragments(s: string): string[] {
  const out = new Set<string>();
  for (const f of forms(s)) {
    if (f.length <= WINDOW) out.add(f);
    else {
      for (let i = 0; i + WINDOW <= f.length; i++) {
        const w = f.slice(i, i + WINDOW);
        // Padding such as 000000 is not evidence of anything.
        if (new Set(w).size >= 3) out.add(w);
      }
    }
  }
  return [...out];
}

/** The bytes of a binary value (ArrayBuffer, typed array, DataView, or a byte array), else null. */
function bytesOf(v: unknown): Uint8Array | null {
  if (v instanceof ArrayBuffer) return new Uint8Array(v);
  if (ArrayBuffer.isView(v)) return new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
  if (Array.isArray(v) && v.length > 0 && v.every((x) => Number.isInteger(x) && x >= 0 && x <= 255)) {
    return Uint8Array.from(v as number[]);
  }
  return null;
}

/** Every text a persisted value can be read as (lower-cased): the value, and for bytes the decoded text, hex and base64. */
function textsOf(v: unknown): string[] {
  const b = bytesOf(v);
  if (b) {
    const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
    return [
      new TextDecoder("utf-8").decode(b),
      String.fromCharCode(...b),
      new TextDecoder("utf-16le").decode(b.slice(0, b.length - (b.length % 2))),
      String.fromCharCode(...b.filter((x) => x !== 0)),
      hex,
      btoa(String.fromCharCode(...b)),
      [...b].join(","),
    ].map((t) => t.toLowerCase());
  }
  return [typeof v === "string" ? v.toLowerCase() : String(v)];
}

/** The oracle: probes found in a list of persisted values (names only, never values). */
function leaks(values: unknown[], probes: string[], whole: string[]): string[] {
  const found: string[] = [];
  const texts = values.flatMap(textsOf);
  for (const p of probes) {
    const frags = fragments(p);
    const nums = new Set(forms(p));
    if (texts.some((t) => frags.some((f) => t.includes(f)) || nums.has(t))) found.push(p.slice(0, 4));
  }
  for (const w of whole) {
    const fs = forms(w);
    if (texts.some((t) => fs.some((f) => t.includes(f)))) found.push(w.slice(0, 4));
  }
  return found;
}

// ---- schema against the allowlist -------------------------------------------
type Table = { name: string; columns: string[] };

async function realSchema(): Promise<Table[]> {
  const t = await DB.prepare(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE '_cf_%' ORDER BY name",
  ).all<{ name: string }>();
  const out: Table[] = [];
  for (const { name } of t.results) {
    const c = await DB.prepare(`SELECT name FROM pragma_table_info('${name}')`).all<{ name: string }>();
    out.push({ name, columns: c.results.map((r) => r.name) });
  }
  return out;
}

/** Tables or columns outside the allowlist, and tables or columns the allowlist expects but the schema lacks. */
function schemaProblems(schema: Table[]): string[] {
  const tables = allow.tables as Record<string, string[]>;
  const optional = new Set((allow.optional_tables as string[]).map((x) => x.toLowerCase()));
  const problems: string[] = [];
  for (const t of schema) {
    const cols = tables[t.name];
    if (!cols) {
      problems.push(`unexpected table ${t.name}`);
      continue;
    }
    for (const c of t.columns) if (!cols.includes(c)) problems.push(`unexpected column ${t.name}.${c}`);
    for (const c of cols) if (!t.columns.includes(c)) problems.push(`missing column ${t.name}.${c}`);
  }
  const have = new Set(schema.map((t) => t.name));
  for (const name of Object.keys(tables)) {
    if (!have.has(name) && !optional.has(name.toLowerCase())) problems.push(`missing table ${name}`);
  }
  return problems;
}

// ---- a database that records every bound value --------------------------------
function recordingDb(binds: unknown[], corrupt?: (args: unknown[]) => unknown[]): D1Database {
  return new Proxy(DB, {
    get(t, p) {
      if (p === "prepare") {
        return (sql: string) => {
          const stmt = t.prepare(sql);
          return new Proxy(stmt, {
            get(s, q) {
              if (q === "bind") {
                return (...args: unknown[]) => {
                  const a = corrupt ? corrupt(args) : args;
                  binds.push(...a);
                  return s.bind(...a);
                };
              }
              const v = Reflect.get(s, q, s);
              return typeof v === "function" ? v.bind(s) : v;
            },
          });
        };
      }
      const v = Reflect.get(t, p, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
}

async function wholeDatabase(): Promise<unknown[]> {
  const values: unknown[] = [];
  for (const t of await realSchema()) {
    const rows = (await DB.prepare(`SELECT * FROM "${t.name}"`).all()).results;
    for (const r of rows) values.push(...Object.values(r as Record<string, unknown>));
  }
  return values;
}

async function drive(binds: unknown[], corrupt?: (a: unknown[]) => unknown[]): Promise<void> {
  const db = recordingDb(binds, corrupt);
  const clickEnv: ClickEnv = {
    DB: db,
    SITE_HOST: "links.example.test",
    LINK_IDS: JSON.stringify(["example-one"]),
    CLICK_LIMITER: fakeLimiter(),
  };
  const who = [
    { ip: PROBE_V4, ua: PROBE_UA },
    { ip: PROBE_V6, ua: PROBE_UA },
    { ip: REALISTIC[0]!, ua: REALISTIC[2]! },
    { ip: REALISTIC[1]!, ua: REALISTIC[3]! },
  ];
  for (let i = 0; i < who.length; i++) {
    const { ip, ua } = who[i]!;
    const headers = {
      "User-Agent": ua,
      "CF-Connecting-IP": ip,
      "CF-IPCountry": "US",
      "X-Forwarded-For": ip,
      "X-Real-IP": ip,
      "True-Client-IP": ip,
      "X-Probe": PROBE_HDR,
    };
    const c = await handleClick(
      new Request("https://worker.example.test/e/c/example-one", {
        method: "POST",
        headers: { ...headers, "Ping-From": "https://links.example.test/" },
      }),
      clickEnv,
    );
    expect(c.status).toBe(204);
    const v = await handleView(beacon({ headers }), makeEnv({ DB: db }), NOW + i * 1000);
    expect(v.status).toBe(204);
  }
  await rollupClosedDays({ DB: db, TOP_SOURCES: "1000", MIN_CELL: "1" }, NOW + 3 * 86_400_000);
}

beforeEach(async () => {
  await clearAll();
  await DB.prepare("DELETE FROM click_events").run();
});

describe("no raw address, header or user agent is persisted", () => {
  it("the_real_schema_is_exactly_the_allowlist", async () => {
    const schema = await realSchema();
    // Loose collection: the migrations really created the tables.
    expect(schema.length).toBeGreaterThanOrEqual(8);
    expect(schema.map((t) => t.name)).toContain("view_events");
    // Strict assertion.
    expect(schemaProblems(schema)).toEqual([]);
  });

  it("no_bound_value_and_nothing_in_the_database_holds_request_data_in_any_form", async () => {
    const binds: unknown[] = [];
    await drive(binds);
    // Loose collection: all three jobs really wrote through the recording DB,
    // and the raw and aggregate tables hold rows.
    expect(binds.length).toBeGreaterThanOrEqual(20);
    for (const t of ["click_events", "view_events", "visitor_seen", "view_daily"]) {
      const n = await DB.prepare(`SELECT COUNT(*) AS n FROM ${t}`).first<{ n: number }>();
      expect(n?.n, t).toBeGreaterThanOrEqual(1);
    }
    // Strict assertions.
    expect(leaks(binds, PROBES, REALISTIC)).toEqual([]);
    expect(leaks(await wholeDatabase(), PROBES, REALISTIC)).toEqual([]);
  });
});

describe("the guards bite", () => {
  it("schema_check_flags_renamed_and_added_columns_and_tables", () => {
    const base = Object.entries(allow.tables as Record<string, string[]>).map(([name, columns]) => ({
      name,
      columns,
    }));
    expect(schemaProblems(base)).toEqual([]);
    for (const col of ["peer_ip", "client_address", "http_user_agent", "request_headers"]) {
      const bad = base.map((t) => (t.name === "view_events" ? { ...t, columns: [...t.columns, col] } : t));
      expect(schemaProblems(bad), col).toEqual([`unexpected column view_events.${col}`]);
    }
    const extra = [...base, { name: "request_log", columns: ["id"] }];
    expect(schemaProblems(extra)).toEqual(["unexpected table request_log"]);
    // The other direction: a missing table or column must fail too.
    expect(schemaProblems(base.filter((t) => t.name !== "view_events"))).toEqual(["missing table view_events"]);
    const noCol = base.map((t) =>
      t.name === "view_events" ? { ...t, columns: t.columns.filter((c) => c !== "country") } : t,
    );
    expect(schemaProblems(noCol)).toEqual(["missing column view_events.country"]);
    // Cloudflare's own bookkeeping tables may be absent.
    expect(schemaProblems(base.filter((t) => t.name !== "d1_migrations" && t.name !== "_cf_KV"))).toEqual([]);
  });

  it("oracle_flags_every_transformed_form_of_each_probe", () => {
    for (const p of PROBES) {
      const stored = [
        p,
        p.toUpperCase(),
        utf8hex(p),
        b64(p),
        b64url(p),
        encodeURIComponent(p),
        p.slice(0, 8), // truncated
        p.slice(3, 11), // a middle fragment of a split copy
        `prefix-${p}-suffix`,
      ];
      for (const s of stored) expect(leaks([s], PROBES, []), `${p.slice(0, 4)} as ${s.slice(0, 6)}`).not.toEqual([]);
    }
    const n = v4Int(PROBE_V4)!;
    for (const s of [n, String(n), "0x" + n.toString(16), n.toString(16)]) {
      expect(leaks([s], PROBES, []), String(s)).not.toEqual([]);
    }
    // Resolver spellings of the planted address, including the reviewer's legacy octal one.
    expect(v4Spellings(PROBE_V4)).toContain("0313.0.0161.0323");
    for (const sp of [
      "0313.0.0161.0323",
      "0xcb.0x00.0x71.0xd3",
      "0xcb.0.113.211",
      "203.0.29139",
      "203.29139",
      "031300070723",
      "::ffff:203.0.113.211",
      "::ffff:cb00:71d3",
      "203.000.113.211",
    ]) {
      expect(leaks([sp], PROBES, []), sp).not.toEqual([]);
      expect(leaks([`ip=${sp};`], PROBES, []), sp).not.toEqual([]);
    }
    // Real percent-encoding: every dot and colon is encoded.
    expect(pctAll(PROBE_V4)[0]).toBe("203%2E0%2E113%2E211");
    for (const p of [PROBE_V4, PROBE_V6]) {
      for (const e of pctAll(p)) expect(leaks([e], PROBES, []), e).not.toEqual([]);
    }
    expect(leaks(["203%2E0%2E113%2E211"], PROBES, [])).not.toEqual([]);
    for (const sp of ["2001:0db8:abcd:0000:0000:0000:0000:0042", "2001db8abcd00000000000000000042".padStart(32, "0"), "[2001:db8:abcd::42]", "2001:db8:abcd::42%eth0"]) {
      expect(leaks([sp], PROBES, []), sp).not.toEqual([]);
    }
    // Ordinary stored values are not flagged.
    expect(leaks(["chrome", "linux", "desktop", "bluesky", "US", 1792000000000, "h", "ok"], PROBES, REALISTIC)).toEqual([]);
  });

  it("binary_values_are_normalised_to_bytes_before_the_scan", () => {
    const bytes = new TextEncoder().encode(PROBE_V4);
    const wide = new Uint16Array([...bytes]); // 2 bytes per unit; low bytes still spell the probe
    const forms2: unknown[] = [
      bytes,
      bytes.buffer,
      new DataView(bytes.buffer),
      Array.from(bytes), // how D1 returns a BLOB column
      new TextEncoder().encode(utf8hex(PROBE_V4)), // bytes of the hex text
      new TextEncoder().encode(b64(PROBE_V4)), // bytes of the base64 text
      new Uint8Array(bytes.buffer, 4, 6), // a view onto part of the buffer
      wide,
    ];
    for (const [i, v] of forms2.entries()) expect(leaks([v], PROBES, []), `form ${i}`).not.toEqual([]);
    // Binary that is not request data is not flagged.
    expect(leaks([new Uint8Array([1, 2, 3, 4]), new ArrayBuffer(8)], PROBES, REALISTIC)).toEqual([]);
  });

  it("binary_data_injected_through_the_real_bind_path_is_caught", async () => {
    const binds: unknown[] = [];
    await drive(binds, (args) => {
      const a = [...args];
      if (typeof a[3] === "string" && a.length === 19) a[3] = new TextEncoder().encode(PROBE_UA).buffer;
      return a;
    });
    expect(binds.some((b) => b instanceof ArrayBuffer)).toBe(true);
    expect(leaks(binds, PROBES, REALISTIC)).not.toEqual([]);
  });

  it("a_leak_through_the_real_handlers_is_seen_by_the_recording_database", async () => {
    // Corrupt the 4th bound value (src) of every statement to a hex copy of the
    // planted address, as a regression that stored it encoded would.
    const binds: unknown[] = [];
    await drive(binds, (args) => {
      const a = [...args];
      if (typeof a[3] === "string" && a.length === 19) a[3] = utf8hex(PROBE_V4);
      return a;
    });
    expect(leaks(binds, PROBES, REALISTIC)).not.toEqual([]);
    expect(leaks(await wholeDatabase(), PROBES, REALISTIC)).not.toEqual([]);
  });
});
