# ADR 0011: No marketing email to leads

- Status: accepted (decided 2026-11-24; the privacy page text to match is still to be written)
- Date: 2026-11-24
- Source: maintainer decision of 2026-11-24 (privacy decisions); `docs/privacy.md`; `docs/superpowers/specs/2026-10-23-reach-around-design.md` (section 4.5, 4.6, 4.11)

## Context

A partner who opens the media kit gives an email address. That address was given to unlock a kit, not to join a mailing list. Treating it as a marketing list would break the expectation it was given under.

## Decision

Lead emails are used to unlock the kit and to tell the owner who looked. They are not used for marketing, and the list is never shared. The gate stores an unverified email with a clear notice on the form. Emailed verification is planned for a later version. The owner alert email is masked by default.

Leads are kept 12 months by default, are configurable, and can be removed by an owner command that deletes a lead by email. They stay out of aggregate tables, exports meant for sharing, and dashboards.

## Consequences

- The kit cannot double as a newsletter funnel. That is intended.
- Because the address is unverified, a typo or a made-up address is stored as given. Do not treat a lead as a confirmed contact.
- The privacy page needs a fixed notice block for the gate, and a contact for deletion requests whenever the gate or alerts are on.
- INFERRED: the exact masking format of the alert email is not described in the sources read for this record.
