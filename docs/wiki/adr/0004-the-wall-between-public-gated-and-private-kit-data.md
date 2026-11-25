# ADR 0004: The wall between public, gated and private kit data

- Status: accepted
- Date: 2026-11-07
- Source: `docs/kit.md` (section "The wall between the two"); `docs/superpowers/specs/2026-10-23-reach-around-design.md` (section 4.11); PR #287 (media kit builder, B2); issue #256

## Context

The media kit shows some numbers to anyone and keeps others (partners, rates, demographics, case studies) for people who pass a gate. A mistake that leaks a gated field into the public page cannot be taken back. The design also bars public display of data a platform's terms forbid showing.

## Decision

Every kit field has one visibility: public, gated or private. The public page is rendered from a public projection and the gated page from a gated projection, so a template cannot reach a field outside its projection. Private fields are never rendered anywhere.

The two pages go to two separate directories, and the gated one is never part of the public deploy. The gated page is uploaded with the Worker, which serves it only against a valid unlock. The build checks the separation by filesystem identity, writes the gated directory owner-only, refuses a symbolic link in its place, and searches the public output for gated values before anything is written. The gated page makes no remote requests.

## Consequences

- A template or config mistake fails the build instead of publishing a gated value.
- Two output roots and two upload paths add steps to every deploy, and the deploy guide has to say so plainly.
- The private PDF is made on the owner's machine from the same data and is never uploaded.
- Data collected through private OAuth scopes (for example YouTube Analytics) is never accepted into a public field.
