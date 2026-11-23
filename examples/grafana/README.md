<!-- SPDX-License-Identifier: Apache-2.0 -->
# Example Grafana dashboards

Dashboards as code for the analytics report (issue #727). Three dashboards, a
read-only SQLite data source, a synthetic sample so they render before any real
data exists, and a rootless Podman recipe for a separate Grafana. Read
[docs/grafana.md](../../docs/grafana.md) first: Grafana reads one small file that
`reach-around grafana-export` makes from the floored aggregates, never a
Cloudflare token or the Worker's database.

| File | What it is |
|---|---|
| `dashboards/overview.json` | Counts over time: likely-human views and link clicks per day, traffic classes, clicks per 100 views, collection gaps, the cutover marker |
| `dashboards/clicks.json` | Link clicks by link (printed rows only), share, and per day |
| `dashboards/watch.json` | Twitch and Kick side by side, the same four panels each |
| `provisioning/datasources/reach-around.yaml` | The data source: `frser-sqlite-datasource`, file `/data/grafana.sqlite3`, opened `mode=ro`, no secret |
| `provisioning/dashboards/reach-around.yaml` | Loads the JSON above, not editable from the UI |
| `quadlet/` | The Podman recipe: a `.container`, `.network` and `.volume` for systemd user units |
| `sample/make_sample_export.py` | A synthetic export for an example creator. A fixed seed, no real data |
| `build_dashboards.py` | Writes `dashboards/*.json` (the Twitch and Kick side come from one function). `--check` fails when a file is stale |

## What the dashboards show, and what they cannot yet

They read only the `grafana_*` tables in `docs/grafana.md`. Three things you
might expect are not in that store, so they are not drawn:

- **Clicks by group.** The store has clicks per link id, not per group, and the
  group rollup with its Under 5 rule (#678) is not built. `clicks.json` shows
  printed rows per link and says so. When #678 adds a table, add one panel that
  reads only its printed rows.
- **Link versus icon clicks.** The export carries `destination_kind`, but the
  store drops it. Same fix: a store change first, then a panel.
- **The kit funnel (gate, unlock, inquiry) and follower counts over time.**
  `kit_events` and the counts history are not in the store. "Counts over time"
  here means views and clicks per day.

Twitch and Kick are the link ids `twitch` and `kick`. If your watch links use
other ids, edit `WATCH` in `build_dashboards.py` and run it again.

Counts follow the report's floor: a row under 5 is never stored alone (small rows
become one `(below the floor)` line, which these dashboards call "Small links,
merged"), and stat panels show a count of 1 to 4 as **Under 5**. One consequence
of the shared floor rule: on a day when any small row is merged, the largest row
joins the merged line too, so a link can be missing from a day's bars. That is the
rule, not a data loss.

Dark is the default theme (`GF_USERS_DEFAULT_THEME=dark`). Grafana lets a user
change their own theme in their profile; there is no setting that removes the
choice.

## See it with sample data (no live data needed)

```sh
cd examples/grafana
mkdir -p ~/reach-around-grafana/data
SCRATCH="$(mktemp -d)"
python3 sample/make_sample_export.py > "$SCRATCH/sample-export.json"
reach-around grafana-export --input "$SCRATCH/sample-export.json" \
    --output ~/reach-around-grafana/data/grafana.sqlite3 \
    --cutover "$(date -u -d '-25 days' +%F)"
cp -r provisioning dashboards ~/reach-around-grafana/
```

The generator ends on today's UTC day by default, so the default 30 day range has
data. Pass `--end YYYY-MM-DD` for a fixed, repeatable file. The sample has one
whole day with no collection and one partial day (marked in red), a rare link
that the floor merges away, and a cutover marker.

For your own data, make the export as described in [report.md](../../docs/report.md) and
point `--output` at the same path. Rebuild it from a fresh export; the
dashboards read the file on every query.

## Run it: rootless Podman, a separate Grafana

The recipe starts its own Grafana on its own port. It joins no existing network,
shares no volume and never touches another Grafana.

| Piece | Value |
|---|---|
| Container, network, volume | `reach-around-grafana` for each |
| Port | `127.0.0.1:3737` (loopback only; 3000 stays free) |
| Docker socket | none; rootless Podman only |
| Image | `grafana/grafana` 12.4.15, pinned by tag and digest |
| Plugin | `frser-sqlite-datasource` 4.0.6, installed into the named volume at first start (needs outbound access to grafana.com once) |
| Hardening | read-only root file system, all capabilities dropped, no new privileges, own tmpfs |
| Host files | `~/reach-around-grafana/{data,provisioning,dashboards}`, all mounted read-only |

```sh
# 1. the admin password, as a Podman secret (never in a file in this repo)
head -c 24 /dev/urandom | base64 | podman secret create reach-around-grafana-admin - > /dev/null

# 2. install the units for your user and start
mkdir -p ~/.config/containers/systemd
cp quadlet/* ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start reach-around-grafana.service

# 3. open http://127.0.0.1:3737 and sign in as admin
podman secret inspect --showsecret reach-around-grafana-admin --format '{{.SecretData}}'
```

Step 3 prints the password to your terminal once; keep it in your password
manager. To stop it: `systemctl --user stop reach-around-grafana.service`.
To remove everything it made:
`podman volume rm reach-around-grafana`, `podman network rm reach-around-grafana`,
`podman secret rm reach-around-grafana-admin`, and delete the unit files.

The `%h` in the unit files is systemd's home directory. If your files live
elsewhere, change the three host paths in `quadlet/reach-around-grafana.container`.

This recipe is for your own machine. Running it on a server is an owner decision
and is not part of this change.

## Checks

```sh
python3 build_dashboards.py --check          # JSON matches the generator
python3 -m pytest tests/test_grafana_dashboards.py
```

The tests parse the JSON, check the expected panels, run every query against a
store built from the sample, scan every file here for secrets, hosts, a callsign
or a grid square, and check the Podman recipe (own network, named volume,
loopback port, pinned image, no socket).
