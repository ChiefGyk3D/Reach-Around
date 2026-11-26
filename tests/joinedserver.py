# SPDX-License-Identifier: Apache-2.0
"""A loopback test server that never outlives its test.

``ThreadingHTTPServer`` starts its handler threads as daemons, so
``server_close()`` returns while a handler may still be writing to a client that
already gave up (a size cap, a timeout, a refused answer). That late write fails,
``socketserver`` prints a traceback to ``sys.stderr``, and by then ``sys.stderr``
belongs to whatever test runs next (#858, #859: the traceback landed in the stderr
assertion of ``tests/test_report.py``). This server keeps its handler threads
non-daemon, so ``server_close()`` joins every one of them before the fixture
returns, and it says nothing about a client that went away: a closed connection
is the point of those tests, not a failure. Any other handler error still prints.
"""

from __future__ import annotations

import sys
from http.server import ThreadingHTTPServer
from typing import Any


class JoinedHTTPServer(ThreadingHTTPServer):
    """Joins its handler threads on ``server_close()``; silent on a gone client."""

    daemon_threads = False
    block_on_close = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], OSError):
            return
        super().handle_error(request, client_address)
