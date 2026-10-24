# SPDX-License-Identifier: Apache-2.0
"""One module per CLI command; each defines ``register(subparsers)``.

``reach_around.cli.main`` imports every module here, so adding a command never
edits a shared file.
"""
