# DPIA screening checklist (template)

An engineering template, not legal advice. See the [README](README.md). A data
protection impact assessment (DPIA) is a written risk assessment that some
processing requires. This page helps you decide whether yours needs one. The
answers below are what the software does by default; change an answer if your
deployment differs, and have a lawyer confirm the conclusion against your
regulator's published list.

Reviewer: OWNER. Date: OWNER. Software version checked: OWNER.

## Conclusion (default setup)

With the shipped defaults the processing is small: aggregate audience
measurement, a short lead list of business contacts and no person-level
profile. A full DPIA is probably not needed (INFERRED). Re-screen at every
trigger in the second table.

## Screening questions

| # | Question | Default answer | Where it comes from |
|---|---|---|---|
| 1 | Systematic, large-scale monitoring of people? | No. No IP stored, no analytics cookie, no person-level profile. | Code today |
| 2 | Profiling or scoring that affects people? | No. | Code today |
| 3 | Special-category data (health, beliefs and similar) or criminal data? | Not collected. The inquiry form has free text, so say in the form that no sensitive data should be sent. | Code today; the notice is PLANNED |
| 4 | Large-scale processing of any data? | No, on a small site. Your traffic decides; OWNER: answer for your numbers. | INFERRED |
| 5 | Matching or combining datasets? | No. Kit events carry an hour-rounded time and a lead id only. | Code today |
| 6 | Vulnerable people (children, employees)? | Not targeted. Leads are business contacts. OWNER: confirm for your audience. | INFERRED |
| 7 | New technology or innovative use? | No. Proof of work and a bot check are ordinary protections. The beacon is PLANNED. | INFERRED |
| 8 | Does the processing decide whether someone gets a service or right? | The gate asks for an email to get the kit; no one is refused a service. | Code today |
| 9 | Does data leave your region? | Yes, to Cloudflare (and to a chat or mail provider if alerts are on). See [records-of-processing.md](records-of-processing.md). | LAWYER |
| 10 | What reduces the risk? | No IP stored, daily hashed id deleted at day end, small cells combined, region rather than city in stored rows, GPC and DNT honored, purge jobs, a delete command for leads, alerts off by default. Planned: masked alerts by default, a leads database of its own, an alert-copy purge. | Code today; PLANNED where marked |

## Triggers: stop and re-screen

1. Person-level collector data. Any import or API route that brings individual
   viewers, followers, chatters or subscribers (names, handles, ids) into the
   system. Aggregates stay out of scope; person-level fields do not.
2. Demographic joins. Joining audience demographics with leads, views or any
   identifier, or building segments of individuals.
3. A beacon design change: exact timestamps, longer raw retention, a stable
   identifier across days, cross-site tracking, fingerprinting, storing IP, or
   loading the bot check for visitors who send GPC or DNT.
4. Selling, sharing or trading the lead list, or sending marketing mail to
   leads.
5. Sensitive data collected through the inquiry form or any new free-text
   field.
6. A new processor, a second controller, or a new region of operation.
7. A large jump in traffic or in the size of the lead list.

## Review

Next review: OWNER (suggest on any trigger above, and once a year).
Sign-off: OWNER, date.
