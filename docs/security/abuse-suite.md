# Abuse suite and mutation check (media-kit gate)

Issue #270, part of the media kit epic. This page says how to run the two checks, what they prove, and what they do not.

## The abuse suite

Where: `worker/test/abuse/`. Run it with the rest of the Worker tests, or alone:

```
cd worker
npm ci --ignore-scripts
npx vitest run test/abuse --maxWorkers=2
```

Every test goes through the real Worker entry point (`worker.fetch`) with fakes for the outside world: the Turnstile check and the alert sink are fakes over the global `fetch`, the database is the test D1, every address and host is a documentation placeholder. Nothing here touches a network.

| File | Abuse case | What it asserts |
|---|---|---|
| `ab3-gate-bypass.test.ts` | AB-3 gate bypass, method confusion | No route, method, path spelling or credential placement shows the gated page without the unlock cookie (query, header, form, wrong cookie name, duplicate cookies, oversized cookie); every gate and view response is `private, no-store`; the kill switch closes everything per request with no database, KV, vendor or alert call; the gate POST needs an exact Origin |
| `ab4-token.test.ts` | AB-4 tokens and sessions | Altered payloads, flipped signature bits, truncated or extra segments, other versions and keys, non-canonical encodings, validly signed but impossible claims, expiry and issue time boundaries, deleted leads, revoked sessions (lead floor and the kit-wide floor for unstored unlocks), key rotation; the inquiry form refuses the same cookies; refused cookies never spend the view budget |
| `ab8-privacy.test.ts` | AB-8 personal data | No table holds a client address, user agent, token or cookie; only `leads` holds an address; logs hold only closed vocabulary words; responses never echo what the visitor sent, a secret, a setting name or a stack; a failing database, KV or vendor looks exactly like any other refusal |
| `ab9-alerts.test.ts` | AB-9 alert abuse, AB-1 injection | Hostile text (mentions, links, markdown, bidi and zero width characters, line injection) is inert in the owner's alert; look-alike webhook addresses are never called; addresses that could add a recipient or header are refused before anything is sent; floods of unlocks and inquiries are bounded by the per-minute and daily alert budgets and by dedup |
| `ab10-cost.test.ts` | AB-10 and AB-2 cost and quota | A refused request makes zero database statements, KV reads, vendor calls and alerts; a body is bounded before it is read (exact cap, chunked, lying length); per-client and per-network allowances cap vendor calls; the hourly and daily budgets stop at their limit and a refused call writes nothing; lead caps hold under concurrency and the answer does not depend on whether the address was stored; one token or one address used many times at once unlocks or stores exactly once |

`harness.ts` holds the shared fakes and request builders. Cases for the emailed unlock link (AB-5) and the private PDF (AB-11, covered by `tests/test_kit_pdf_*.py`) belong to features that are not part of this slice.

## The mutation check

A passing test suite only shows the tests pass. It does not show they would fail if a control were removed. The mutation check does that: it breaks the gate on purpose, one named edit at a time, and requires a test to fail for each.

```
cd worker && npm ci --ignore-scripts && cd ..
python3 -I scripts/mutation-check.py --validate     # list still matches the source (no node needed)
python3 -I scripts/mutation-check.py                # the full run, about 30 minutes
python3 -I scripts/mutation-check.py --only token-expiry-not-checked
python3 -I scripts/mutation-check.py --skip-test test/abuse   # what the older tests alone catch
```

The list is `scripts/mutation-check.toml`. Each `[[mutation]]` has an id, the file, the exact text to find (it must occur exactly once), its replacement, a one-line description, the test files that must catch it, and the `// CONTROL:<id>` markers in the source it exercises. The markers are the repository's existing convention: one marker per guard line. A Python test (`tests/test_mutation_check.py`) fails when a marker in `worker/src/kit` or `worker/src/alerts` has no mutation, when an edit no longer applies exactly once, or when a named test file is missing.

How a run works:

1. The script copies `worker/` (source, tests, migrations, config) into a fresh temporary directory and links the installed `node_modules`. The repository's working tree is never written, and the temporary directory is deleted at the end, even on error.
2. It runs the tests unmutated first. If that does not pass, it stops: a mutation result would mean nothing.
3. For each mutation it applies the one edit in the copy, runs the named test files under `nice -n 19` with 2 workers, restores the file, and records the result.
4. A mutation is KILLED when at least one test fails, SURVIVED when none does, and ERROR when the run itself broke (a timeout, a missing report, a suite that failed to load). An error is never counted as a kill.
5. The result is a table. The exit code is non-zero if any mutation survived without a recorded reason, or errored.

A survivor has two possible answers: add a test that fails without the control, or record `equivalent = "reason"` in the list when the edit cannot change anything a caller can see or another layer covers it. The harness warns when an entry marked equivalent is in fact killed, so a stale claim gets removed.

The list has 136 mutations: 134 are expected to be killed and 2 are recorded as equivalent (each with its reason). The other five earlier equivalent claims were re-verified one by one (#927): each turned out to change something a caller can see (a limiter or counter spent before the refusal, a replay answer instead of the budget answer, a signed oversized token accepted) and now has a test that kills it. The kill-switch mutation on the shared guard is not equivalent: the inquiry routes answer the kill switch with the complete killed page, and a test pins that whole response.

The inquiry budget is an attempt bound. A unit is reserved before delivery is tried and is never given back, so a failed delivery keeps its unit spent and the global budget bounds the work the form can cause, even when every alert channel is down. The accepted availability trade-off is a global denial: anyone who can unlock the gate can spend the whole day's inquiry budget with valid posts, most cheaply while delivery is failing, and every other visitor gets the temporary "unavailable" answer until the next UTC day (the kit pages stay up). After a failed delivery the visitor also sees the "not sent" page and the day's unit is gone. The owner sees it only in the Worker logs (`send` `failed` `provider`, `send` `over_budget` `route_budget`); the `alert_budget_near` health alert is a hint, since it travels over the same alert channels and the inquiry counter has no health condition of its own. A mutation that adds a refund back is in the list and is expected to be killed.

## What it proves

- Each listed control is covered by a test that fails when that control is removed or weakened in the specific way listed (a flipped comparison, an off-by-one boundary, a skipped check, a fail-open error path, a widened allowlist).
- The suite does not depend on test order or on a particular clock, apart from where a test names a clock.

## What it does not prove

- It is not a complete mutation test. The list is hand written: it covers the gate, token, session, limits, body, input, guard, inquiry, Turnstile, log and alert-sanitizer code, and only the edits someone thought of.
- A killed mutation shows a test notices that edit, not that the control is sufficient. The threat model (`threat-model.md`) and the independent security review (issue #271) are where sufficiency is judged.
- Equivalent mutations are judged by a person; the recorded reasons are the evidence and are open to challenge.
- The fakes stand in for Turnstile, the alert providers and Cloudflare's rate limiter. Their real behavior (for example Turnstile's eventual consistency) is covered by the notes in `worker/src/kit/gate.ts`, not by this suite.
- The Worker runs on Cloudflare; the tests run in the local Workers test runtime.
