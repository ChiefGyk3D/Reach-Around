// The only network code. Same-origin paths under /__edit/api/ only. The key
// goes in one header; every failure comes out as an ApiError.
import { t } from "./strings.js";

export class ApiError extends Error {
  constructor(status, code, message, path, extra = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.path = path;
    this.state = extra.state === undefined ? null : extra.state;
    this.retryAfter = extra.retryAfter === undefined ? 0 : extra.retryAfter;
  }
}

const PREFIX = "/__edit/api/";

function plain(value, limit) {
  return typeof value === "string" && value.length > 0 && value.length <= limit
    ? value
    : "";
}

// Retry-After in whole seconds, 1 to 3600; anything else is 0 (unknown).
function waitSeconds(reply) {
  const raw = reply.headers && reply.headers.get ? reply.headers.get("Retry-After") : "";
  const seconds = /^[0-9]{1,4}$/.test(raw || "") ? Number(raw) : 0;
  return seconds >= 1 && seconds <= 3600 ? seconds : 0;
}

async function failure(reply) {
  let body = null;
  try {
    body = await reply.json();
  } catch {
    body = null;
  }
  const error = body && typeof body === "object" ? body.error : null;
  const code = plain(error && error.code, 64) || "http_" + reply.status;
  const fallback = reply.status === 401 ? t("error.unauthorized") : t("error.generic");
  const message = plain(error && error.message, 300) || fallback;
  const path = plain(error && error.path, 200);
  const fresh = body && typeof body === "object" ? body.state : null;
  const state = fresh && typeof fresh === "object" ? fresh : null;
  return new ApiError(reply.status, code, message, path, {
    state,
    retryAfter: reply.status === 429 ? waitSeconds(reply) : 0,
  });
}

export function createClient(getToken, fetcher = fetch) {
  async function call(route, method, payload) {
    if (!/^[a-z/]+$/.test(route)) {
      throw new ApiError(0, "bad_route", t("error.generic"), "");
    }
    const headers = { "X-RA-Token": getToken(), Accept: "application/json" };
    const init = {
      method,
      headers,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      referrerPolicy: "no-referrer",
    };
    if (payload !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(payload);
    }
    let reply;
    try {
      reply = await fetcher(PREFIX + route, init);
    } catch {
      throw new ApiError(0, "network", t("error.network"), "");
    }
    if (!reply.ok) {
      throw await failure(reply);
    }
    try {
      return await reply.json();
    } catch {
      throw new ApiError(reply.status, "bad_reply", t("error.generic"), "");
    }
  }
  return {
    get: (route) => call(route, "GET"),
    post: (route, payload) => call(route, "POST", payload === undefined ? {} : payload),
  };
}
