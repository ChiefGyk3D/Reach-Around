// SPDX-License-Identifier: Apache-2.0
//
// Matrix via the client-server API: PUT an m.room.message event with a fresh
// transaction id, the access token as a Bearer header, and a plain-text body
// (msgtype m.text, no formatted_body, so nothing is rendered as HTML).
//
// INFERRED from the Matrix client-server specification, not exercised here:
// PUT /_matrix/client/v3/rooms/{roomId}/send/m.room.message/{txnId} answers 200
// with an event id. Check the first real delivery in the smoke test.

import { deliver } from "./http";
import type { Channel } from "./types";
import { validateHomeserver, validateRoomId, validateToken } from "./validate";

function newTxnId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

export function createMatrixChannel(
  homeserver: unknown,
  roomId: unknown,
  accessToken: unknown,
  allowedHosts: readonly string[],
): Channel | null {
  const host = validateHomeserver(homeserver, allowedHosts);
  const room = validateRoomId(roomId);
  const token = validateToken(accessToken);
  if (host === null || room === null || token === null) return null;
  return {
    id: "matrix",
    textMode: "plain",
    send: (message, deps) =>
      deliver(
        deps,
        `https://${host}/_matrix/client/v3/rooms/${encodeURIComponent(room)}/send/m.room.message/${newTxnId()}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ msgtype: "m.text", body: message.text }),
        },
      ),
  };
}
