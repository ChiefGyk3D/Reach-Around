// SPDX-License-Identifier: Apache-2.0
//
// Discord via an incoming webhook: one JSON POST with a plain-text summary.
// allowed_mentions.parse is empty, so no text can ping anyone.
//
// INFERRED from Discord's webhook documentation, not exercised against the
// service here: POST <webhook url> with {"content", "allowed_mentions"} answers
// 204 (200 with ?wait=true). Check the first real delivery in the smoke test.

import { deliver } from "./http";
import type { Channel } from "./types";
import { validateDiscordWebhook } from "./validate";

export function createDiscordChannel(webhookUrl: unknown): Channel | null {
  const url = validateDiscordWebhook(webhookUrl);
  if (url === null) return null;
  return {
    id: "discord",
    textMode: "markdown",
    send: (message, deps) =>
      deliver(deps, url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: message.text, allowed_mentions: { parse: [] } }),
      }),
  };
}
