# Token sink: store rotated collector tokens in Doppler

`reach-around counts refresh` normally keeps token refresh manual: when a
Twitch, Instagram, Threads or TikTok token needs refreshing it prints a
`token-health` line and the owner stores the new value by hand. This opt-in sink
lets an unattended run store the rotated value itself (issue 464).

Off by default. With no flag nothing changes: no sink, manual refresh, the
health line, and `auto_refresh: true` is still refused (`refresh_needs_sink`).

## Use

```
export REACH_AROUND_DOPPLER_PROJECT=<project>   # injected, not built in
export REACH_AROUND_DOPPLER_CONFIG=dev          # use dev until the owner says prd
# preferred: the API token on one line of stdin, never argv
reach-around counts refresh --config site.yaml --token-sink doppler --doppler-token-stdin
# kept for compatibility: the REACH_AROUND_DOPPLER_TOKEN variable (see below)
```

`--doppler-project` and `--doppler-config` override the two variables (names are
not secret). There is no option for the token itself, and no option or variable
for the service address: the sink always talks to `https://api.doppler.com`. The sources must also set
`auto_refresh: true` as before.

## What it guarantees

- Store, then verify. The sink writes the value, reads it back, and compares the
  SHA-256 digests in memory. Only then does the source use the new token. A
  restricted or masked secret cannot be read back, so it counts as unverified.
- The value is never printed, logged, returned, put in an exception or `repr`,
  passed on argv, or written to disk. The update call's answer echoes every
  value; the adapter drops it unread. The request is never replayed.
- Failure is reported, never success. A failed or unverified store fails the
  source (`refresh_store_failed` for Twitch and TikTok), keeps the old token in
  use for the run, writes no new number, and prints the usual
  `token-health: ... kind=rotated_unstored` line plus
  `secret-sink: source=<slug> kind=<word>`, where `<word>` is `store_failed`,
  `verify_failed`, `verify_mismatch` or `bad_input`. Instagram and Threads keep
  using the old token for the count, as today, and print their
  `refresh_store_failed` health line.

## Know before you switch it on

- The API token: stdin is the preferred source. If the environment variable is
  used, the program removes it from its own environment at startup, before
  the arguments are read, and holds it privately only for the sink. A run that
  does not use the sink drops it. That narrows the exposure; it cannot undo what the shell,
  service manager or launcher already did with the variable.
- Single writer. Doppler's update call returns no revision that a read could be
  pinned to (docs.doppler.com, secrets update, read 2026-11-19), so the
  read-back proves the value was stored at read time, not that no one wrote
  after it. Give each project and config one owner: this collector run. The tool
  takes an advisory lock per project and config (a file in one fixed state
  folder under the account's home from the system user database, not from
  `HOME` or `XDG_STATE_HOME`, holding only a process id) before any other work,
  and refuses a second run of itself at once. The folder and file must be owned
  by the account, the folder mode 0700 and the file mode 0600 with one link;
  anything else is refused with `unsafe_state`. A local lock cannot stop writers
  on other hosts or accounts, so keep people and other jobs out of that config.

- A write that lands but fails verification (or times out) leaves the new value
  in the manager while the run keeps the old one. Twitch and TikTok rotate the
  refresh token on use, so check the two secrets after a `rotated_unstored` line
  and authorize again if they disagree with what the platform accepts.
- Instagram and Threads: the new `token_expires` day lives in the site config,
  not the secret manager. The sink prints it (`set token_expires to <date>`);
  update the config by hand, or the next run asks for a refresh inside the same
  window and the platform may refuse it as too young (`refresh_refused`).

## Doppler access plan (owner, dev only)

Doppler API facts, VERIFIED on docs.doppler.com, 2026-11-19: write is
`POST https://api.doppler.com/v3/configs/config/secrets` with a JSON body
`{project, config, secrets: {NAME: value}}` and a Bearer token; read back is
`GET /v3/configs/config/secret?project=&config=&name=` returning `value.raw`.
The exact permission names a service account needs for these two calls are not
stated on those pages (INFERRED: read and write of secrets in one config).

Create one service account token for this job, limited to the `dev`
environment and the one config named above, with read and write on secrets in
that config and nothing else. Do not use a personal or CLI token. Promotion to
`prd` is a separate step the owner decides.
