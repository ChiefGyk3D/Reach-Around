# Open risks

This is the ranked plain-language view of Open threats in `reach-around.threat-model.json`. The ranking weighs impact and the prerequisites needed to cause harm. The full descriptions, STRIDE categories, and data-flow context are in the model. The source content is `docs/security/threat-model.md`.

49 threats in four diagrams: 23 Mitigated, 26 Open.

| Scope | Total | Mitigated | Open | Accepted | Not Applicable |
| --- | ---: | ---: | ---: | ---: | ---: |
| All diagrams | 49 | 23 | 26 | 0 | 0 |

## Ranking

| Rank | T-id | Open risk | Severity | Cheapest next mitigation |
| ---: | --- | --- | --- | --- |
| 1 | T-03 | Gated content crosses into the public deploy. | Critical | Complete the deploy refusal, private Worker store, and authorization path. |
| 2 | T-02, T-19 | Credential exposure has an unclear blast radius or recovery path. | Critical | Add the inventory, scope manifests, and tested revocation runbook. |
| 3 | T-04, T-21 | A forged or first-redeemed bearer token unlocks the kit. | Critical | Complete atomic redemption, revocation, binding policy, and deployment. |
| 4 | T-06 | A platform response leaks a secret. | Critical | Ship typed collector allowlists and sentinel tests. |
| 5 | T-03 | The data-flow view still has the gated-content boundary open. | Critical | Complete the same deploy, store, and authorization controls across the data path. |
| 6 | T-02, T-19 | The data-flow view still has credential recovery open. | Critical | Complete the same inventory, scope, and recovery controls across the data path. |
| 7 | T-18, T-11 | Audience or terms policy can be bypassed. | High | Build one audience projection and require tested, expiring approvals. |
| 8 | T-09 | Supply-chain and release verification is incomplete. | High | Add invariant tests, install-script refusal, SBOM, provenance, and artifact verification. |
| 9 | T-01 | Anonymous mail can relay spam or spend quota. | High | Require fixed delivery, Turnstile, body limits, budgets, and a fail-closed outage policy. |
| 10 | T-05, T-07 | Bots or distributed traffic pollute data or exhaust capacity. | High | Complete verification, provider-fronted limits, and final storage ceilings. |
| 11 | T-13 | Alternate provider hostnames can expose an unintended endpoint. | High | Add the Host allowlist, explicit default-host setting, and provider checks. |
| 12 | T-14, T-63 to T-70 | Alert or inquiry handling can be abused. | High | Complete the gate deployment and record provider retention behavior. |
| 13 | T-12, T-17 | Lead retention and deletion miss copies. | High | Add the copy inventory, deletion registry, restore test, and provider roll-off record. |
| 14 | T-15 | Snapshot or import failure corrupts reports or exports. | High | Add source labels, stale markers, bounded validation, atomic updates, and spreadsheet neutralization. |
| 15 | T-16 | An export crosses the wrong boundary. | High | Reuse the audience projection and test sentinel leads across every output. |
| 16 | T-10, T-20 | Build confinement and published-image metadata controls are incomplete. | High | Add reserved-name and whole-build tests, then implement the metadata decision. |
| 17 | T-08 | Private Worker responses lack complete header verification. | High | Add Worker response headers, no-referrer behavior, and biting tests. |
| 18 | T-24 | The editor session token can remain visible in browser surfaces. | High | Add browser verification for address-bar clearing and storage absence. |
| 19 | T-26 | Editor media paths and save-time asset checks are incomplete. | High | Connect the shared asset hook and test media links and special files. |
| 20 | T-27 | The planned upload route lacks end-to-end controls and tests. | High | Implement generated names, exclusive writes, caps, metadata policy, and upload tests. |
| 21 | T-29 | Editor state or media listing can disclose secrets. | High | Schema-allowlist state and add sentinel and listing tests. |
| 22 | T-34 | Preview isolation is partial. | High | Build the real preview and test frame and navigation isolation in a browser. |
| 23 | T-25 | Future uploads and media counts lack resource caps. | Medium | Add upload-rate and media-count controls with tests. |
| 24 | T-36 | An editor session can remain running after the owner leaves. | Medium | Add the idle timeout, page exit control, and visible session state. |
| 25 | T-39, T-40 | The Live page slice is not built, so player embeds and third-party code are unverified. | Medium | Build click-to-load players from named hosts only, with page tests. |
| 26 | T-46 | The hourly lead purge schedule is unobserved because nothing is deployed. | Medium | Deploy the Worker and observe the heartbeat. |

## Status rule and limits

Open means the needed control is planned, only partly present, depends on an undecided policy, or lives in a provider account. Tested local controls are Mitigated in the model even when the relevant service has not been deployed. This model does not verify live Cloudflare, Mailgun, Turnstile, source-control, DNS, or platform-account settings.
