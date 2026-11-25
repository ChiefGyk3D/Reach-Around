# ADR 0003: The instance repo pins the engine

- Status: accepted
- Date: 2026-10-24
- Source: `docs/instance-repos.md`; issue #126 (instance repository template and model); issue #78

## Context

A creator's configuration, copy, partner list and branding are personal. The engine is public. Mixing them would put personal data in public code, or force every user to fork the engine.

## Decision

Reach Around is split in two. The engine is a public, versioned repository released as a signed container image. The instance is a separate private repository holding `site.yaml`, images, overrides and the deploy workflow. The instance contains no engine code and says only which engine release to use.

Three ways to consume the engine were weighed. A pinned container image was chosen over a git submodule and over `pip install` of a tagged release. Pinning the image digest means the bytes you build with are the bytes that were verified. Because the image holds the site builder but not the Worker, the instance also pins the commit of the same release tag and checks out only the `worker/` folder.

## Consequences

- Upgrading the engine is a deliberate change in the instance repo, visible in its history.
- No Python toolchain is needed on the runner, and the build runs with no network, no capabilities and a read-only root.
- Two pins (image digest and Worker commit) must move together. The reusable deploy workflow and the instance template exist to keep them aligned.
- Nothing personal belongs in the engine repo. The instance repo holds configuration only, never snapshots, leads or tokens.
