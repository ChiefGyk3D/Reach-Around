// SPDX-License-Identifier: Apache-2.0
/**
 * Pre-route guard for the kit: steps 1 to 3 of the guard order. Everything here
 * is synchronous and free: no database, KV, network, or body read. The routes
 * (not in this module) run the later, costlier steps.
 */
import { isKitKilled, type KitEnv } from "./env";

/** Fixed bytes. Nothing in it comes from the request or names the switch. */
const KILLED_HTML = "<!doctype html><title>Unavailable</title><p>Temporarily unavailable.";

export function killedResponse(): Response {
  return new Response(KILLED_HTML, {
    status: 503,
    headers: {
      "Retry-After": "3600",
      "Cache-Control": "private, no-store",
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": "default-src 'none'",
    },
  });
}

const SAFE_METHODS: ReadonlySet<string> = new Set(["GET", "HEAD", "OPTIONS"]);

export interface GuardOptions {
  /** Upper-case methods the kit accepts. */
  methods: readonly string[];
  /**
   * Required: omitting it is a compile error. Return true to allow. A throw,
   * a non-true result, or (for an untyped caller) a missing hook refuses a
   * state-changing method.
   */
  originCheck: (request: Request) => boolean;
}

export type GuardDecision = { allow: true } | { allow: false; response: Response };

function refuse(status: number, extra?: Record<string, string>): GuardDecision {
  return {
    allow: false,
    response: new Response(null, { status, headers: { "Cache-Control": "private, no-store", ...extra } }),
  };
}

/**
 * Kill switch first, read from `env` on this request and before anything else
 * of the request is touched, so a killed kit gives one identical answer for
 * every route and method. Then the method allow-list, then the origin hook.
 */
export function guardRequest(request: Request, env: Pick<KitEnv, "KIT_KILL">, opts: GuardOptions): GuardDecision {
  if (isKitKilled(env)) return { allow: false, response: killedResponse() }; // CONTROL:kill-switch

  const method = request.method.toUpperCase();
  if (!opts.methods.includes(method)) {
    return refuse(405, { Allow: opts.methods.join(", ") });
  }

  let ok = false;
  try {
    ok = typeof opts.originCheck === "function" ? opts.originCheck(request) === true : SAFE_METHODS.has(method); // CONTROL:origin
  } catch {
    ok = false;
  }
  if (!ok) return refuse(403);
  return { allow: true };
}

/**
 * An origin hook for a fixed allow-list. A state-changing request must carry an
 * Origin header that exactly matches one entry; a missing or "null" Origin is
 * refused. Safe methods pass.
 */
export function originAllowList(allowed: readonly string[]): (request: Request) => boolean {
  const set = new Set(allowed);
  return (request) => {
    if (SAFE_METHODS.has(request.method.toUpperCase())) return true;
    const origin = request.headers.get("Origin");
    return origin !== null && set.has(origin);
  };
}
