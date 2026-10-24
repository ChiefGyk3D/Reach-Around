# Local preview

```
reach-around serve --config site.yaml [--port 8000] [--host 127.0.0.1]
```

Open `http://127.0.0.1:8000/` for the site, or `http://127.0.0.1:8000/__preview`
to see it at desktop width and at 390 px phone width side by side.

- The site is built into a temporary directory that is deleted when you stop
  the server (Ctrl+C).
- The server checks the config file and the package templates and static files
  every 500 ms and rebuilds when one changes. The side-by-side page reloads
  itself.
- If the config is broken, the browser shows the error (HTTP 500). Fix the file
  and the next rebuild clears it.
- It binds to loopback only (`127.0.0.1`, `::1`, `localhost`). Any other host,
  including `0.0.0.0` and LAN addresses, is refused.
- It serves only GET and HEAD, sends `Cache-Control: no-store`, and never serves
  anything outside the temporary output directory.
- The `--overrides` option arrives with template overrides (Task 6D). The
  function already accepts it and ignores it until then.

This is a development tool. Never expose it, and never deploy it. The preview
page is not part of `reach-around build` output.
