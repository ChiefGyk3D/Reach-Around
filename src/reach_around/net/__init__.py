# SPDX-License-Identifier: Apache-2.0
"""Outbound network helpers: hostname and address checks, one hardened fetch.

A deliberately small, standard-library precursor of the egress guard planned
in docs/superpowers/plans/2026-10-23-plan-2-numbers.md (Tasks 22 and 23). The
``Transport`` seam in ``egress`` is where that guard's transport replaces
``HttpsTransport``; ``fetch_json`` and its callers do not change.
"""
