// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { classifyUserAgent, primaryLanguage, type UaClass } from "../src/ua";

type Row = [
  name: string,
  ua: string,
  browser: UaClass["browser"],
  major: number | null,
  os: UaClass["os"],
  device: UaClass["device"],
];

const WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)";
const MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)";
const LIN = "Mozilla/5.0 (X11; Linux x86_64)";
const WK = "AppleWebKit/537.36 (KHTML, like Gecko)";

const TABLE: Row[] = [
  // Chrome
  ["chrome windows", `${WIN} ${WK} Chrome/128.0.6613.84 Safari/537.36`, "chrome", 128, "windows", "desktop"],
  ["chrome macos", `${MAC} ${WK} Chrome/127.0.0.0 Safari/537.36`, "chrome", 127, "macos", "desktop"],
  ["chrome linux", `${LIN} ${WK} Chrome/126.0.0.0 Safari/537.36`, "chrome", 126, "linux", "desktop"],
  ["chrome ubuntu", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "chrome", 120, "linux", "desktop"],
  ["chrome android phone", `Mozilla/5.0 (Linux; Android 14; Pixel 8) ${WK} Chrome/128.0.6613.99 Mobile Safari/537.36`, "chrome", 128, "android", "mobile"],
  ["chrome android phone sm", `Mozilla/5.0 (Linux; Android 13; SM-S918B) ${WK} Chrome/125.0.0.0 Mobile Safari/537.36`, "chrome", 125, "android", "mobile"],
  ["chrome android tablet", `Mozilla/5.0 (Linux; Android 13; SM-X710) ${WK} Chrome/124.0.0.0 Safari/537.36`, "chrome", 124, "android", "tablet"],
  ["chrome android tablet pixel", `Mozilla/5.0 (Linux; Android 14; Pixel Tablet) ${WK} Chrome/127.0.0.0 Safari/537.36`, "chrome", 127, "android", "tablet"],
  ["chrome android desktop-mode", `Mozilla/5.0 (X11; Linux x86_64) ${WK} Chrome/128.0.0.0 Safari/537.36`, "chrome", 128, "linux", "desktop"],
  ["chrome on ios iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/127.0.6533.107 Mobile/15E148 Safari/604.1", "chrome", 127, "ios", "mobile"],
  ["chrome on ios ipad", "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.153 Mobile/15E148 Safari/604.1", "chrome", 126, "ios", "tablet"],
  ["chrome ipod", "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1", "chrome", 120, "ios", "mobile"],
  ["chromeos", "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", "chrome", 128, "chromeos", "desktop"],
  ["chromeos arm", "Mozilla/5.0 (X11; CrOS aarch64 15662.76.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36", "chrome", 114, "chromeos", "desktop"],
  ["chrome windows 7", "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36", "chrome", 109, "windows", "desktop"],
  ["chrome version capped", `${WIN} ${WK} Chrome/123456.0.0.0 Safari/537.36`, "chrome", 999, "windows", "desktop"],
  // Edge
  ["edge windows", `${WIN} ${WK} Chrome/128.0.0.0 Safari/537.36 Edg/128.0.2739.42`, "edge", 128, "windows", "desktop"],
  ["edge macos", `${MAC} ${WK} Chrome/127.0.0.0 Safari/537.36 Edg/127.0.2651.74`, "edge", 127, "macos", "desktop"],
  ["edge linux", `${LIN} ${WK} Chrome/126.0.0.0 Safari/537.36 Edg/126.0.2592.87`, "edge", 126, "linux", "desktop"],
  ["edge android phone", `Mozilla/5.0 (Linux; Android 14; Pixel 8) ${WK} Chrome/128.0.0.0 Mobile Safari/537.36 EdgA/127.0.2651.102`, "edge", 127, "android", "mobile"],
  ["edge android tablet", `Mozilla/5.0 (Linux; Android 13; SM-X710) ${WK} Chrome/126.0.0.0 Safari/537.36 EdgA/126.0.2592.99`, "edge", 126, "android", "tablet"],
  ["edge on ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 EdgiOS/127.2651.96 Mobile/15E148 Safari/605.1.15", "edge", 127, "ios", "mobile"],
  ["edge legacy", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36 Edge/18.19044", "edge", 18, "windows", "desktop"],
  // Firefox
  ["firefox windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0", "firefox", 130, "windows", "desktop"],
  ["firefox macos", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0", "firefox", 129, "macos", "desktop"],
  ["firefox linux", "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0", "firefox", 128, "linux", "desktop"],
  ["firefox ubuntu", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0", "firefox", 127, "linux", "desktop"],
  ["firefox android phone", "Mozilla/5.0 (Android 14; Mobile; rv:130.0) Gecko/130.0 Firefox/130.0", "firefox", 130, "android", "mobile"],
  ["firefox android tablet", "Mozilla/5.0 (Android 13; Tablet; rv:129.0) Gecko/129.0 Firefox/129.0", "firefox", 129, "android", "tablet"],
  ["firefox on ios iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/130.0 Mobile/15E148 Safari/605.1.15", "firefox", 130, "ios", "mobile"],
  ["firefox on ios ipad", "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/129.0 Mobile/15E148 Safari/605.1.15", "firefox", 129, "ios", "tablet"],
  ["firefox esr", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0", "firefox", 115, "windows", "desktop"],
  // Safari
  ["safari macos", `${MAC} AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15`, "safari", 17, "macos", "desktop"],
  ["safari macos 16", `${MAC} AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15`, "safari", 16, "macos", "desktop"],
  ["safari iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "safari", 17, "ios", "mobile"],
  ["safari ipad", "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "safari", 17, "ios", "tablet"],
  ["safari ipod", "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6 Mobile/15E148 Safari/604.1", "safari", 15, "ios", "mobile"],
  ["safari ipados desktop mode", `${MAC} AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15`, "safari", 17, "macos", "desktop"],
  ["safari ios 12", "Mozilla/5.0 (iPhone; CPU iPhone OS 12_5_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1.2 Mobile/15E148 Safari/604.1", "safari", 12, "ios", "mobile"],
  // Opera
  ["opera windows", `${WIN} ${WK} Chrome/127.0.0.0 Safari/537.36 OPR/113.0.0.0`, "opera", 113, "windows", "desktop"],
  ["opera macos", `${MAC} ${WK} Chrome/126.0.0.0 Safari/537.36 OPR/112.0.0.0`, "opera", 112, "macos", "desktop"],
  ["opera linux", `${LIN} ${WK} Chrome/125.0.0.0 Safari/537.36 OPR/111.0.0.0`, "opera", 111, "linux", "desktop"],
  ["opera android phone", `Mozilla/5.0 (Linux; Android 14; Pixel 8) ${WK} Chrome/126.0.0.0 Mobile Safari/537.36 OPR/82.0.0.0`, "opera", 82, "android", "mobile"],
  ["opera mini android", `Mozilla/5.0 (Linux; Android 13; SM-A546B) ${WK} Chrome/125.0.0.0 Mobile Safari/537.36 OPR/80.4.0.0`, "opera", 80, "android", "mobile"],
  ["opera gx", `${WIN} ${WK} Chrome/127.0.0.0 Safari/537.36 OPR/113.0.0.0 (Edition GX)`, "opera", 113, "windows", "desktop"],
  ["opera on ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) OPiOS/16.0.15 Mobile/15E148 Safari/9537.53", "opera", 16, "ios", "mobile"],
  ["opera presto", "Opera/9.80 (Windows NT 6.1; U; en) Presto/2.12.388 Version/12.18", "opera", 9, "windows", "desktop"],
  // Samsung Internet
  ["samsung phone", `Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S918B) ${WK} SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36`, "samsung", 25, "android", "mobile"],
  ["samsung tablet", `Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-X710) ${WK} SamsungBrowser/24.0 Chrome/117.0.0.0 Safari/537.36`, "samsung", 24, "android", "tablet"],
  ["samsung older", `Mozilla/5.0 (Linux; Android 12; SAMSUNG SM-G991B) ${WK} SamsungBrowser/19.0 Chrome/102.0.5005.125 Mobile Safari/537.36`, "samsung", 19, "android", "mobile"],
  // Brave (token present in some builds)
  ["brave token", `${WIN} ${WK} Chrome/128.0.0.0 Safari/537.36 Brave/128.1.69.153`, "brave", 128, "windows", "desktop"],
  ["brave android token", `Mozilla/5.0 (Linux; Android 14; Pixel 8) ${WK} Chrome/128.0.0.0 Mobile Safari/537.36 Brave/128.1.69.150`, "brave", 128, "android", "mobile"],
  // Engine only (named engine, no recognizable browser)
  ["android webview", `Mozilla/5.0 (Linux; Android 13; SM-S918B; wv) ${WK} Version/4.0 Chrome/124.0.0.0 Mobile Safari/537.36`, "other", null, "android", "mobile"],
  ["ios in-app webview", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148", "other", null, "ios", "mobile"],
  ["ios in-app ipad", "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148", "other", null, "ios", "tablet"],
  ["ie11", "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko", "other", null, "windows", "desktop"],
  ["unknown gecko linux", "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101", "other", null, "linux", "desktop"],
  // Unknown OS
  ["windows phone", "Mozilla/5.0 (Windows Phone 10.0; Android 6.0.1; Microsoft; Lumia 950) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/52.0.2743.116 Mobile Safari/537.36 Edge/15.15254", "edge", 15, "android", "mobile"],
  ["freebsd firefox", "Mozilla/5.0 (X11; FreeBSD amd64; rv:128.0) Gecko/20100101 Firefox/128.0", "firefox", 128, "linux", "desktop"],
  ["playstation", "Mozilla/5.0 (PlayStation; PlayStation 5/2.26) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0 Safari/605.1.15", "safari", 13, "other", "other"],
  ["smart tv", "Mozilla/5.0 (SMART-TV; LINUX; Tizen 7.0) AppleWebKit/537.36 (KHTML, like Gecko) 108.0.5359.1/7.0 TV Safari/537.36", "other", null, "other", "other"],
];

const BOTS: Array<[string, string]> = [
  ["googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"],
  ["googlebot smartphone", "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"],
  ["bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"],
  ["yahoo slurp", "Mozilla/5.0 (compatible; Yahoo! Slurp; http://help.yahoo.com/help/us/ysearch/slurp)"],
  ["duckduckbot", "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)"],
  ["baiduspider", "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)"],
  ["facebook preview", "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"],
  ["slackbot", "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"],
  ["twitterbot", "Twitterbot/1.0"],
  ["discordbot", "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"],
  ["whatsapp", "WhatsApp/2.23.20.0"],
  ["curl", "curl/8.5.0"],
  ["wget", "Wget/1.21.4"],
  ["python requests", "python-requests/2.32.3"],
  ["python urllib", "Python-urllib/3.12"],
  ["go http", "Go-http-client/2.0"],
  ["java", "Java/17.0.9"],
  ["okhttp", "okhttp/4.12.0"],
  ["node fetch", "node-fetch/1.0 (+https://github.com/bitinn/node-fetch)"],
  ["axios", "axios/1.7.2"],
  ["headless chrome", `${LIN} ${WK} HeadlessChrome/128.0.0.0 Safari/537.36`],
  ["phantomjs", "Mozilla/5.0 (Unknown; Linux x86_64) AppleWebKit/538.1 (KHTML, like Gecko) PhantomJS/2.1.1 Safari/538.1"],
  ["lighthouse", `${LIN} ${WK} Chrome/126.0.0.0 Safari/537.36 Chrome-Lighthouse`],
  ["uptime monitor", "Mozilla/5.0 (compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)"],
];

describe("classifyUserAgent table", () => {
  it("has at least 60 real-world strings", () => {
    expect(TABLE.length + BOTS.length).toBeGreaterThanOrEqual(60);
  });

  it.each(TABLE)("%s", (_name, ua, browser, major, os, device) => {
    const got = classifyUserAgent(ua);
    expect(got.browser).toBe(browser);
    expect(got.browserMajor).toBe(major);
    expect(got.os).toBe(os);
    expect(got.device).toBe(device);
  });

  it.each(BOTS)("bot %s gives other everywhere", (_name, ua) => {
    expect(classifyUserAgent(ua)).toEqual({
      browser: "other",
      browserMajor: null,
      os: "other",
      device: "other",
      engineOnly: false,
    });
  });

  it("engineOnly is set for webviews and bare engines, never for named browsers", () => {
    for (const [name, ua, browser] of TABLE) {
      expect(classifyUserAgent(ua).engineOnly, name).toBe(
        browser === "other" && /AppleWebKit|Gecko|Trident|Presto/.test(ua),
      );
    }
    expect(classifyUserAgent(TABLE.find((r) => r[0] === "android webview")?.[1] ?? "").engineOnly).toBe(true);
  });
});

describe("ordering rules", () => {
  const ua = (name: string) => classifyUserAgent(TABLE.find((r) => r[0] === name)?.[1] ?? "");

  it("rule: Edge wins over Chrome", () => {
    expect(ua("edge windows").browser).toBe("edge");
    expect(ua("edge android phone").browser).toBe("edge");
  });
  it("rule: Opera wins over Chrome", () => {
    expect(ua("opera windows").browser).toBe("opera");
    expect(ua("opera android phone").browser).toBe("opera");
  });
  it("rule: Samsung Internet wins over Chrome", () => {
    expect(ua("samsung phone").browser).toBe("samsung");
  });
  it("rule: Brave wins over Chrome", () => {
    expect(ua("brave token").browser).toBe("brave");
  });
  it("rule: Chrome wins over Safari", () => {
    expect(ua("chrome windows").browser).toBe("chrome");
    expect(ua("chrome android phone").browser).toBe("chrome");
  });
  it("rule: CriOS is Chrome, not Safari", () => {
    expect(ua("chrome on ios iphone").browser).toBe("chrome");
    expect(ua("chrome on ios iphone").os).toBe("ios");
  });
  it("rule: FxiOS is Firefox, not Safari", () => {
    expect(ua("firefox on ios iphone").browser).toBe("firefox");
  });
  it("rule: EdgiOS is Edge, not Safari", () => {
    expect(ua("edge on ios").browser).toBe("edge");
  });
  it("rule: iOS wins over macOS ('like Mac OS X')", () => {
    expect(ua("safari iphone").os).toBe("ios");
  });
  it("rule: Android wins over Linux", () => {
    expect(ua("chrome android phone").os).toBe("android");
  });
  it("rule: CrOS wins over Linux", () => {
    expect(ua("chromeos").os).toBe("chromeos");
  });
  it("rule: Macintosh (iPadOS desktop mode) is macos desktop", () => {
    const got = ua("safari ipados desktop mode");
    expect([got.os, got.device]).toEqual(["macos", "desktop"]);
  });
  it("rule: Android without Mobile is a tablet", () => {
    expect(ua("chrome android tablet").device).toBe("tablet");
    expect(ua("chrome android phone").device).toBe("mobile");
  });
  it("rule: iPad is a tablet, iPhone is mobile", () => {
    expect(ua("safari ipad").device).toBe("tablet");
    expect(ua("safari iphone").device).toBe("mobile");
  });
  it("rule: bots are screened before browsers", () => {
    expect(classifyUserAgent(BOTS[1]?.[1] ?? "").browser).toBe("other");
  });
});

describe("hostile and degenerate input", () => {
  const ALL_OTHER: UaClass = {
    browser: "other",
    browserMajor: null,
    os: "other",
    device: "other",
    engineOnly: false,
  };
  const CHROME = `${WIN} ${WK} Chrome/128.0.6613.84 Safari/537.36`;

  it.each([
    ["null", null],
    ["empty", ""],
    ["undefined", undefined],
    ["number", 42],
    ["object", { toString: () => CHROME }],
    ["array", [CHROME]],
    ["boolean", true],
  ])("%s gives all other", (_n, v) => {
    expect(classifyUserAgent(v as unknown as string | null)).toEqual(ALL_OTHER);
  });

  it("input longer than 512 chars gives all other, exactly 512 is parsed", () => {
    const pad = (n: number) => `${CHROME} ${"x".repeat(n - CHROME.length - 1)}`;
    expect(pad(512)).toHaveLength(512);
    expect(classifyUserAgent(pad(512)).browser).toBe("chrome");
    expect(classifyUserAgent(pad(513))).toEqual(ALL_OTHER);
    expect(classifyUserAgent(CHROME + " ".repeat(100_000))).toEqual(ALL_OTHER);
  });

  it("whitespace, control, null bytes and unicode do not throw", () => {
    for (const s of [
      " ",
      "\n\r\t",
      "\u0000".repeat(50),
      "‮ Chrome/128",
      "Chrome/٣٢",
      "Мозилла/5.0 (Окна) Хром/128",
      "🦊".repeat(100),
      "\ud800",
      "%00%0d%0a",
      "'; DROP TABLE click_events;--",
      "<script>alert(1)</script>",
    ]) {
      expect(() => classifyUserAgent(s)).not.toThrow();
    }
  });

  it("a huge version number is capped at 999 and a lone token has no version", () => {
    expect(classifyUserAgent("Mozilla/5.0 (Windows NT 10.0) Chrome/99999999999999999999").browserMajor).toBe(999);
    expect(classifyUserAgent("Mozilla/5.0 (Windows NT 10.0) Chrome/").browser).toBe("other");
  });

  it("repeated tokens return fast", () => {
    for (const s of ["Chrome/".repeat(70), "Version/1 ".repeat(50), "(".repeat(500), "Edg/".repeat(120)]) {
      const t = performance.now();
      classifyUserAgent(s);
      expect(performance.now() - t).toBeLessThan(50);
    }
  });

  it("does not echo the input into a thrown error", () => {
    const secret = "SENTINEL-UA-9f3a";
    const got = JSON.stringify(classifyUserAgent(`Mozilla/5.0 ${secret} Firefox/130.0`));
    expect(got).not.toContain(secret);
  });
});

describe("property: closed enums, input never echoed", () => {
  const BROWSERS = ["chrome", "edge", "firefox", "safari", "opera", "samsung", "brave", "other"];
  const OSES = ["windows", "macos", "linux", "android", "ios", "chromeos", "other"];
  const DEVICES = ["mobile", "tablet", "desktop", "other"];
  const FRAGMENTS = [
    "Mozilla/5.0", "Windows NT 10.0", "Macintosh", "X11", "Linux", "Android 14", "iPhone", "iPad",
    "CrOS", "Chrome/128", "CriOS/127", "FxiOS/130", "EdgiOS/127", "Edg/128", "OPR/113",
    "SamsungBrowser/25", "Brave/128", "Firefox/130", "Version/17 Safari/605", "Mobile", "Googlebot",
    "SENTINEL-abc123", "(", ")", ";", " ", "\u0000", "é", "💥",
  ];

  function rng(seed: number) {
    let s = seed;
    return () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 2 ** 32;
    };
  }

  it("10,000 random UAs give only closed-enum values and never contain the input", () => {
    const rand = rng(1234);
    for (let i = 0; i < 10_000; i++) {
      const n = 1 + Math.floor(rand() * 12);
      let input = "";
      for (let j = 0; j < n; j++) {
        input += FRAGMENTS[Math.floor(rand() * FRAGMENTS.length)] + (rand() < 0.5 ? " " : "");
      }
      const got = classifyUserAgent(input);
      expect(BROWSERS).toContain(got.browser);
      expect(OSES).toContain(got.os);
      expect(DEVICES).toContain(got.device);
      expect(typeof got.engineOnly).toBe("boolean");
      if (got.browserMajor !== null) {
        expect(Number.isInteger(got.browserMajor)).toBe(true);
        expect(got.browserMajor).toBeGreaterThanOrEqual(0);
        expect(got.browserMajor).toBeLessThanOrEqual(999);
      }
      expect(Object.keys(got).sort()).toEqual(["browser", "browserMajor", "device", "engineOnly", "os"]);
      const dump = JSON.stringify(got);
      expect(dump).not.toContain("SENTINEL");
      expect(dump).not.toContain(input.trim() || "\u0001unlikely");
    }
  });

  it("every table row output is in the closed sets (negative: the check bites)", () => {
    const inSets = (g: UaClass) =>
      BROWSERS.includes(g.browser) && OSES.includes(g.os) && DEVICES.includes(g.device);
    for (const [, ua] of [...TABLE, ...BOTS]) expect(inSets(classifyUserAgent(ua))).toBe(true);
    expect(inSets({ ...classifyUserAgent(""), browser: "netscape" as never })).toBe(false);
  });
});

describe("primaryLanguage", () => {
  it.each([
    ["en-US,en;q=0.9", "en"],
    ["en-US,en;q=0.9,de;q=0.4", "en"],
    ["de", "de"],
    ["DE-at", "de"],
    ["fr-CA", "fr"],
    ["zh-Hant-TW", "zh"],
    ["zh-Hans-CN,zh;q=0.9", "zh"],
    ["fil", "fil"],
    ["haw-US", "haw"],
    ["es-419", "es"],
    ["pt-BR;q=0.5", "pt"],
    ["  en-GB  ", "en"],
    ["en;q=0.1", "en"],
    ["ja,en-US;q=0.9", "ja"],
    ["en-US-xy", "en"],
    ["sr-Latn-RS", "sr"],
    ["de,*;q=0.1", "de"],
  ])("%s -> %s", (header, want) => {
    expect(primaryLanguage(header)).toBe(want);
  });

  it.each([
    ["null", null],
    ["empty", ""],
    ["star", "*"],
    ["star first", "*,en"],
    ["x-i-default", "x-i-default"],
    ["i-klingon", "i-klingon"],
    ["single-letter subtag", "en-US-x-private1"],
    ["underscore", "en_US"],
    ["single letter", "e"],
    ["four letters", "abcd"],
    ["digits", "12"],
    ["leading comma", ",en"],
    ["only q", ";q=0.5"],
    ["space inside", "en US"],
    ["trailing dash", "en-"],
    ["subtag too long", "en-abcdefghi"],
    ["subtag too short", "en-a"],
    ["unicode", "ён-US"],
    ["unicode letters", "français"],
    ["null byte", "en\u0000"],
    ["crlf", "en\r\nX-Evil: 1"],
    ["sql", "en'; DROP TABLE x;--"],
    ["script", "<script>"],
    ["undefined", undefined],
    ["number", 7],
  ])("rejects %s -> und", (_n, header) => {
    expect(primaryLanguage(header as unknown as string | null)).toBe("und");
  });

  it("header over 256 chars gives und, exactly 256 is parsed", () => {
    const ok = `en-US,${"x".repeat(250)}`;
    expect(ok).toHaveLength(256);
    expect(primaryLanguage(ok)).toBe("en");
    expect(primaryLanguage(`${ok}x`)).toBe("und");
    expect(primaryLanguage(`en,${"a;q=0.1,".repeat(10_000)}`)).toBe("und");
  });

  it("only ever returns 2-3 lowercase letters or und, never the input", () => {
    const parts = ["en", "EN-us", "q=0.9", ",", ";", "*", "x", "é", "SENTINEL", "-", "zh-Hant", " ", "\u0000", "abc"];
    let s = 99;
    const rand = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    for (let i = 0; i < 5000; i++) {
      let h = "";
      for (let j = 0; j < 1 + Math.floor(rand() * 8); j++) h += parts[Math.floor(rand() * parts.length)];
      const got = primaryLanguage(h);
      expect(got).toMatch(/^(?:[a-z]{2,3}|und)$/);
      if (got !== "und") expect(h.toLowerCase()).toContain(got);
      expect(got).not.toContain("sentinel");
    }
  });
});
