# Personal data breach procedure (template)

An engineering template, not legal advice. See the [README](README.md). It fits
a one-person site. Fill in the contacts now, before you need them. Whether a
given incident must be reported, to whom and by when depends on your country
and role; this page does not decide that, and a lawyer should review it.

## Who decides

OWNER decides whether an incident is a breach, whether it is reported, and who
is told. Backup person if OWNER cannot be reached: OWNER (name one, or write
"none"). Nothing is sent on anyone's behalf; any reporting clock keeps running
while you are unreachable.

## What counts

Loss, leak, alteration or unauthorized access involving personal data: lead
email addresses, page view records, hashed visitor ids, copies in an alert room
or mailbox, a leaked credential that can reach them, or a database export left
public.

## Contacts (fill in)

| Role | Contact |
|---|---|
| Owner | OWNER |
| Backup | OWNER |
| Supervisory authority | REGULATOR (OWNER decides which; ask a lawyer, do not guess) |
| Cloudflare incident contact | OWNER (from your signed terms or account dashboard) |
| Alert provider contact | OWNER |
| Lawyer | OWNER |

## The reporting clock

Many regimes, including the GDPR, set a deadline of 72 hours to notify the
authority from becoming aware of a breach, unless it is unlikely to risk
people's rights. Confirm with a lawyer whether it applies to you and when
"aware" starts for a one-person site. Write down the time you became aware, in
Eastern time and UTC. Record your reasoning whether or not you report. If you
report late, state why.

## Steps

1. Detect. Write down how you found out, what you saw and when. Start the
   timeline.
2. Contain. Revoke or rotate the exposed credential, disable the affected
   route, make any public file private. Change nothing else yet.
3. Preserve evidence before you clean up (list below).
4. Assess. What data, how many people, how sensitive, could it be misused. Hashed
   daily ids and hour-rounded rows lower the risk; lead email addresses raise it.
5. Decide and write down the decision and the reason: report to the authority
   or not, tell the people or not.
6. Notify the authority where required, within the deadline your lawyer
   confirmed: what happened, the categories of data and approximate numbers of
   people and records, your contact, likely effects, what you did.
7. Notify affected people where required. See the next section.
8. Tell processors that need to know: Cloudflare, your alert provider.
9. Fix the cause, then update [records-of-processing.md](records-of-processing.md)
   and this page.
10. Record. Keep the file of the incident, even for incidents you did not report.

## Telling affected people

Plain language, sent directly (email to the address you hold, otherwise a
notice on the site). Say what happened, what data was involved, what it could
mean for them, what you did, what they can do, and OWNER contact for
questions. Leave out exploit detail. Have a lawyer confirm wording and any
country-specific duty.

## What to preserve

- The timeline, with times in Eastern and UTC.
- Cloudflare audit and Worker logs for the window, your secrets manager access
  log and your repository audit log.
- A private copy of the exposed object (never in a repository) and the id of the
  credential, never the secret itself.
- Alert-room messages in scope and screenshots of any public exposure.
- The written decision on reporting, with reasons.
- Copies of every notice you send.

Keep the record for the period OWNER sets with a lawyer's advice.

## Checklist

- [ ] Aware time written down
- [ ] Contained
- [ ] Evidence preserved
- [ ] Data, people and risk assessed
- [ ] Decision on authority and people recorded, with reason
- [ ] Authority notified within the confirmed deadline, or reasoning filed
- [ ] Affected people told where required
- [ ] Processors told
- [ ] Cause fixed, records updated
- [ ] Lessons logged
