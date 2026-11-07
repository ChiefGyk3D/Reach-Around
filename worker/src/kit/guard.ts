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

export interface GuardOptions {
  /** Upper-case methods the kit accepts. */
  methods: readonly string[];
  /** Return true to allow. Injected; absent means no origin check at this layer. A throw refuses. */
  originCheck?: (request: Request) => boolean;
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

  if (!opts.methods.includes(request.method.toUpperCase())) {
    return refuse(405, { Allow: opts.methods.join(", ") });
  }

  if (opts.originCheck) {
    let ok = false;
    try {
      ok = opts.originCheck(request) === true; // CONTROL:origin
    } catch {
      ok = false;
    }
    if (!ok) return refuse(403);
  }
  return { allow: true };
}
