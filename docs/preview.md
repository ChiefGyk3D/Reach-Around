# Local preview

```
reach-around serve --config site.yaml [--port 8000] [--host 127.0.0.1]
                    [--overrides DIR]
```

Open `http://127.0.0.1:8000/` for the site, or `http://127.0.0.1:8000/__preview`
to see it at desktop width and at 390 px phone width side by side.

- The site is built into a temporary directory that is deleted when you stop
  the server (Ctrl+C).
- The server checks the config file, the overrides directory and the package
  templates and static files every 500 ms and rebuilds when one changes. The side-by-side page reloads
  itself.
- If the config is broken, the browser shows the error (HTTP 500). Fix the file
  and the next rebuild clears it.
- It binds to loopback only (`127.0.0.1`, `::1`, `localhost`). Any other host,
  including `0.0.0.0` and LAN addresses, is refused.
- It checks the `Host` header on every request (DNS-rebinding defense). Only
  `localhost`, `127.0.0.1`, `[::1]` and the bound host are answered; anything
  else, or a missing `Host`, gets 421. A request with a foreign `Origin` or
  `Sec-Fetch-Site: cross-site` gets 403.
- It serves only GET and HEAD, sends `Cache-Control: no-store`, and never serves
  anything outside the temporary output directory.
- `--overrides DIR` previews your template and `custom.css` overrides (see
  [the config reference](config.md#instance-overrides-and-security-headers)).
  Files in that directory are watched too. A missing directory is refused at
  start.

This is a development tool. Never expose it, and never deploy it. The preview
page is not part of `reach-around build` output.
