/**
 * The one parser for the visitor's privacy signals (Sec-GPC and DNT), shared
 * by the click, token-mint and page-view routes. No imports on purpose: it
 * depends on nothing, so any route can use it without a cycle.
 */

/** One header value is an opt-out when any comma-separated token, trimmed, is "1" (a proxy may join repeats). */
export function signalOn(value: string | null): boolean {
  // Trim only HTTP optional whitespace (space and tab), not Unicode spaces.
  return value !== null && value.split(",").some((token) => token.replace(/^[ \t]+|[ \t]+$/g, "") === "1");
}

/** True when the request carries Sec-GPC: 1 or DNT: 1 (the only values either signal defines); fails toward privacy. */
export function optedOut(request: Request): boolean {
  return signalOn(request.headers.get("Sec-GPC")) || signalOn(request.headers.get("DNT"));
}
