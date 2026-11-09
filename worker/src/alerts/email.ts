// SPDX-License-Identifier: Apache-2.0
//
// Email behind a provider-neutral interface. Only the disabled provider ships:
// no email provider adapter (Mailgun, Resend, Cloudflare Email) is built yet,
// so an email channel is never "configured" today and nothing is sent. Adding
// a provider means one EmailProvider implementation and one entry in
// EMAIL_PROVIDERS.

import type { Channel, ChannelResult, SendDeps } from "./types";
import { validateRecipient } from "./validate";

export interface EmailMessage {
  to: string;
  subject: string;
  text: string;
}

export interface EmailProvider {
  send(message: EmailMessage, deps: SendDeps): Promise<ChannelResult>;
}

/** The no-op provider: never sends, always reports `unavailable`. */
export const disabledEmailProvider: EmailProvider = {
  send: async () => ({ ok: false, failure: "unavailable" }),
};

/**
 * Providers by the name given in ALERT_EMAIL_PROVIDER. A provider factory
 * takes the env bag and returns a provider, or null when its own secrets are
 * missing. Empty on purpose.
 */
export const EMAIL_PROVIDERS: Readonly<Record<string, (env: Record<string, unknown>) => EmailProvider | null>> = {};

export function createEmailChannel(provider: EmailProvider | null, recipient: unknown): Channel | null {
  const to = validateRecipient(recipient);
  if (provider === null || to === null) return null;
  return {
    id: "email",
    textMode: "plain",
    send: (message, deps) => provider.send({ to, subject: message.subject, text: message.text }, deps),
  };
}
