// SPDX-License-Identifier: Apache-2.0
//
// The one outbound request helper every HTTP adapter uses. It follows no
// redirect, has a hard timeout, never reads the response body, and returns a
// closed failure class. The URL and headers (which may hold a secret) are
// passed to fetch and nowhere else.

import type { ChannelResult, SendDeps } from "./types";

export const DEFAULT_TIMEOUT_MS = 3000;

export async function deliver(deps: SendDeps, url: string, init: RequestInit): Promise<ChannelResult> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const expired = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), deps.timeoutMs);
    });
    const call = deps.fetchFn(url, { ...init, redirect: "error", signal: AbortSignal.timeout(deps.timeoutMs) });
    // If the timer wins, swallow the later outcome of the abandoned call.
    call.catch(() => undefined);
    const res = await Promise.race([call, expired]);
    if (res === "timeout") return { ok: false, failure: "timeout" };
    // The answer is never read; free the connection.
    try {
      await res.body?.cancel();
    } catch {
      // ignore
    }
    const s = res.status;
    if (s >= 200 && s < 300) return { ok: true };
    if (s >= 300 && s < 400) return { ok: false, failure: "redirect" };
    if (s >= 400 && s < 500) return { ok: false, failure: "rejected" };
    if (s >= 500) return { ok: false, failure: "server" };
    return { ok: false, failure: "network" };
  } catch (e) {
    const name = typeof e === "object" && e !== null ? (e as { name?: unknown }).name : undefined;
    if (name === "TimeoutError" || name === "AbortError") return { ok: false, failure: "timeout" };
    return { ok: false, failure: "network" };
  } finally {
    clearTimeout(timer);
  }
}
