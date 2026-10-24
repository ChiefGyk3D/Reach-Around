# Reach Around Plan 2: Numbers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tagged `v0.2.0` that collects daily numbers from the easy platforms into an append-only snapshot store, tracks token health, pulls the Worker's click events into the store, and shows everything on a built-in private dashboard; then (phase 2b) two long-running listeners for Twitch viewer counts and StreamElements tips.

**Architecture:** One small collector per platform sits behind a common interface and a runner that records every run, so a failure is stored as "stale" and never as a zero. Results go into a SQL snapshot store (SQLite default, Postgres optional) with one row per platform, account, metric, day and source. A token registry tracks expiry, refreshes what it can and alerts early. A static dashboard renders from the store with no JavaScript. The two listeners are separate processes that write to the same store.

**Tech Stack:** Python 3.11+, httpx, pydantic v2, Jinja2, PyJWT with cryptography (GitHub App), openpyxl (XLSX import), psycopg 3 (optional `postgres` extra), websockets (optional `listeners` extra), pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-23-reach-around-design.md` (sections 4.7 to 4.11, 5, 6). Roadmap: `docs/superpowers/plans/2026-10-23-roadmap.md` (phase 2 and 2b). Builds on Plan 1: `docs/superpowers/plans/2026-10-23-plan-1-foundation-and-link-page.md` (`SiteConfig`, `load_yaml`, `ConfigError`, the Worker table `click_events(id, ts, link_id, country)` with `ts` in epoch milliseconds, the `reach_around.cli` entry point). Platform facts come from `docs/research/*.md`; anything those notes mark UNVERIFIED is checked by the `live` test in the owning task.

## Global Constraints

- Python 3.11 or newer; tests run on 3.11, 3.12 and 3.13.
- License Apache-2.0; every new source file starts with `# SPDX-License-Identifier: Apache-2.0` (TypeScript: `// SPDX-License-Identifier: Apache-2.0`).
- American English in code, docs and messages.
- The public site is static and works with no JavaScript. The only dynamic server code is the one write-only Worker. The Worker never returns visitor data (emails, clicks, leads).
- Do not store IP addresses. At most a two-letter country code from Cloudflare headers.
- No personal details of the maintainer (address, callsign, grid square, hostnames, machine identifiers, real emails) in the repository, examples, tests or logs. Examples use synthetic data and the placeholder callsign `N0CALL` if one is ever needed.
- No secrets in the repository. Credentials come from the environment or a secrets manager.
- CI calls git-your-ship-together reusable workflows pinned by commit with a version comment: `ChiefGyk3D/git-your-ship-together/.github/workflows/python-ci.yml@8fdb8d106b11aae0f37e893bb57e9bd8e8b0e7b7 # v1.24.0`. The gate job is named `CI green`.
- Every PR links its issue (`Refs #n` or `Closes #n`). Every task has an issue and a board card before work starts.
- Versions are semantic. Tags and releases start with the first container image. While alpha, versions are `0.x`; this plan releases `v0.2.0`.
- Containers build from a `Containerfile`, run rootless as a non-root user, depend on no Docker socket, and pin base images by digest. Both `docker` and `podman` must build and run it.
- The README is marked alpha. Keep that banner until the maintainer removes it.
- Time zone for anything date-based defaults to `America/New_York`.
- Privacy: the site collects the bare minimum and none of it is sensitive, and says so plainly. The disclosure page and any cookie notice are generated from the features that are enabled. Plan 2 adds nothing to the public site.
- **Collectors never log or persist secrets or the Kick stream key.** Tokens, keys, webhook URLs and DSN passwords appear in no log line, exception message, database row, dashboard or alert.
- **A collector failure is recorded as stale, never as zero.** A missing field, an empty response or an error writes no observation and records a failed run.
- **Imported and typed values are flagged as such** (`source` is `imported` or `typed`) everywhere they are stored and shown.
- Observations are append-only: no code updates a stored observation, and the only delete is `Store.purge_expired` for owner-authorized rows past their `expires_at`. A day's value is written only when the day is complete, or when the source reports the day itself.
- **Every observation carries an origin tag** (`public_api`, `owner_authorized_api`, `imported`, `typed`) so the phase 5 display-policy check can refuse a forbidden field on a public page. Public counts are kept forever; owner-authorized data may carry an `expires_at` (platform retention terms, for example YouTube Analytics 30 days) and is purged when it passes.
- **The collector host holds the one credential able to read the Worker's database, and the pull asks only for aggregates**: grouped counts, never rows, never the leads table. Lead emails never enter an aggregate table, the kv table, a log or an export.
- Secret files and databases are created owner-only: files mode 0600, their directory 0700.
- Dependencies are pinned with hashes; regenerate the lockfiles Plan 1 created whenever a task adds one.

## Review Focus

Failure modes the spec implies but no obvious test covers, most likely first. Each has a test in the task that owns the code.

1. **Failure written as zero.** An HTTP 500, an empty body, a missing or non-integer count field, or a bot account that was removed from a room must produce a failed run and no observation; the dashboard shows "stale since" with the last real value, never 0. (Tasks 6, 8, 12, 17)
2. **Kick stream key leak.** `stream.key` must not reach any log level, exception text, database table or dashboard, including on the error path. A sentinel value is searched for everywhere. (Task 14)
3. **Lost or raced rotating refresh token.** A single-use refresh token that is rotated but not persisted, or refreshed twice by two processes, locks the owner out until a manual login. The new token is written and read back before it is used, a write failure raises and flags the token, and concurrent refreshes call the provider once. (Tasks 4, 12, 13)
4. **Partial or repeated data frozen into history.** An incomplete current day (GitHub traffic, Twitch VOD day totals) must not be stored as final, and a retried or repeated Worker event pull must neither double count nor skip. An owner-authorized row past its `expires_at` is purged while public counts survive, and no lead email reaches an aggregate table through the pull. (Tasks 2, 7, 10, 13, 16)
5. **Hostile import file.** A CSV or XLSX with NaN, negative or future values, duplicate rows, formula cells, a zip bomb, a symlink or FIFO path, or one bad row among a thousand good ones is rejected whole, with row numbers, and writes nothing. (Task 15)

---

## File Structure

```
src/reach_around/config/models.py      extended: StoreConfig, Account, Alerts, WorkerEvents (Task 1)
src/reach_around/store/                 Task 2, 3
  __init__.py                           open_store(config, env) -> Store
  models.py                             Source, Observation, Run, SeriesKey, ClickDay
  base.py                               Db protocol, SqlStore (all Store methods over Db)
  sqlite.py, postgres.py                Db adapters
  migrations/NNNN_name.sql              portable SQL, applied in numeric order, gaps allowed
src/reach_around/tokens/                Task 4, 5
  secrets.py, registry.py, notify.py
src/reach_around/collectors/            Task 6 to 14
  base.py, http.py, runner.py, twitch_auth.py
  platforms/<name>.py                   one file per platform, auto-discovered
src/reach_around/imports.py             Task 15
src/reach_around/events/                Task 16, 16F: source.py, cloudflare.py, signed_worker.py, pull.py
src/reach_around/dashboard/             Task 17: build.py, templates/index.html
src/reach_around/listeners/             Task 19, 20: twitch_sampler.py, streamelements.py
src/reach_around/commands/<name>.py     one module per CLI command, auto-discovered
docs/collectors/<platform>.md, docs/numbers.md, docs/education/*.md
tests/store, tests/tokens, tests/collectors, tests/listeners, tests/dashboard, tests/events
```

Adding a collector or a command never edits a shared file: `collectors.base.discover()` imports every module in `collectors/platforms/`, and `cli.main` imports every module in `commands/` and calls its `register(subparsers)`. This is what lets the platform tasks run in parallel.

Shared test helpers (created in Task 6, `tests/collectors/conftest.py`): fixture `ctx` builds a `CollectorContext` with an `httpx.MockTransport` whose handler the test sets, an in-memory `SqliteStore`, `EnvSecrets` over a dict, `today=date(2026, 10, 23)` and `tz=America/New_York`; fixture `captured_logs` captures all `reach_around` and `httpx` records at DEBUG.

---

### Task 1: Numbers configuration

**Files:**
- Modify: `src/reach_around/config/models.py`, `docs/config.md`, `pyproject.toml` (dependencies `httpx`, `PyJWT[crypto]`, `cryptography`, `openpyxl`; extras `postgres = psycopg[binary]`, `listeners = websockets`), the Plan 1 hashed lockfiles
- Test: `tests/config/test_numbers_models.py`

**Interfaces:**
- Consumes: `SiteConfig`, `ConfigError`, `load_site_config` (Plan 1 Task 3).
- Produces (all `extra="forbid"`, secret and account names match slug rules):
  - `StoreConfig(kind: Literal["sqlite","postgres"] = "sqlite", path: str = "data/reach-around.db", dsn_env: str | None = None)`; postgres requires `dsn_env`.
  - `Account(platform: str, id: str, options: dict[str, str | int | bool | list[str]] = {}, secrets: dict[str, str] = {})`; `secrets` maps a logical name (`bot_token`) to a secret NAME matching `^[A-Z][A-Z0-9_]{1,63}$`, never a value.
  - `AlertDestination(kind: Literal["stdout","discord","matrix"], secret: str | None = None, options: dict[str, str] = {})`; `Alerts(destinations: list[AlertDestination] = [stdout], warn_days: list[int] = [14, 7, 3, 1], stale_days: int = 10)`.
  - `WorkerEvents(enabled: bool = False, transport: Literal["d1","signed_worker"] = "d1", account_id: str = "", database_id: str = "", token_secret: str = "CLOUDFLARE_D1_READ_TOKEN", worker_url: str = "", signing_secret: str = "REACH_AROUND_OWNER_SIGNING_KEY")`; `signed_worker` requires an https `worker_url`.
  - `SiteConfig.store: StoreConfig`, `.accounts: list[Account]`, `.alerts: Alerts`, `.worker_events: WorkerEvents`, all defaulted so Plan 1 configs still load.

- [ ] **Step 1: Write failing tests**: `test_pasted_secret_value_refused_and_not_echoed` (`secrets: {bot_token: "ghp_abc123"}` raises `ConfigError`; assert `"ghp_abc123" not in str(error)` and `"ghp_abc123" not in error.message`); `test_secret_name_accepted` (`DISCORD_BOT_TOKEN`); `test_duplicate_platform_and_id_refused`; `test_postgres_requires_dsn_env`; `test_no_dsn_or_password_field_exists` (`"dsn" not in StoreConfig.model_fields` and no field named `password`); `test_defaults_keep_plan_1_example_loading` (`examples/instance/site.yaml`); `test_secret_ref_names_not_in_built_site` (build the example plus one account with `secrets: {bot_token: EXAMPLE_TOKEN}`; `"EXAMPLE_TOKEN"` appears in no output file).
- [ ] **Step 2: Run** `pytest tests/config/test_numbers_models.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the models. The pydantic-to-`ConfigError` conversion from Plan 1 Task 3 must drop `input_value` for any field under `secrets`. Add the four dependencies and extras, regenerate lockfiles, document every new field in `docs/config.md` (extend its fenced `yaml` example so `test_config_doc_example_validates` still passes).
- [ ] **Step 4: Run** `pytest tests/config tests/test_docs_examples.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add store, account and alert configuration`.

### Task 2: Snapshot store core with SQLite

**Files:**
- Create: `src/reach_around/store/{__init__,models,base,sqlite}.py`, `src/reach_around/store/migrations/0001_core.sql`, `tests/store/contract.py`, `tests/store/test_sqlite.py`

**Interfaces:**
- Consumes: `StoreConfig` (Task 1).
- Produces:
  - `class Source(StrEnum)`: `FETCHED = "fetched"`, `IMPORTED = "imported"`, `TYPED = "typed"`.
  - `class Access(StrEnum)`: `PUBLIC = "public"`, `OWNER = "owner_authorized"`.
  - `@dataclass(frozen=True) Observation(platform: str, account: str, metric: str, day: date, value: float, source: Source = Source.FETCHED, access: Access = Access.PUBLIC, expires_at: date | None = None)`; `__post_init__` raises `ValueError` for a non-finite or negative value, a non-slug platform, account or metric, or `expires_at` set on a `PUBLIC` observation. Property `origin -> Literal["public_api","owner_authorized_api","imported","typed"]` (imported and typed win over `access`).
  - `Run(platform: str, account: str, ts: datetime, ok: bool, reason: str | None)` (`ts` timezone-aware UTC); `SeriesKey(platform, account, metric)`; `ClickDay(day: date, link_id: str, country: str, count: int)` (`country` is `""` when unknown).
  - `class Db(Protocol)`: `execute(sql: str, params: Sequence[object] = ()) -> None`, `query(sql: str, params: Sequence[object] = ()) -> list[tuple[object, ...]]`, `transaction() -> ContextManager[None]`. Portable SQL uses `?` placeholders and ISO text for dates and timestamps.
  - `class Store` (implemented by `SqlStore(db: Db)`), also exposing `db: Db`:
    - `migrate() -> int` applies every `migrations/NNNN_*.sql` above the stored version in one transaction each and returns the version; raises `StoreError` if the database is newer than the code.
    - `write(observations: Sequence[Observation]) -> int` inserts rows absent for `(platform, account, metric, day, source)`, atomically, returning the inserted count.
    - `purge_expired(today: date) -> int` deletes only rows with `access = 'owner_authorized'` and `expires_at < today`, returning the count; the only delete on `observations`.
    - `series(key: SeriesKey, *, since: date | None = None) -> list[Observation]` one row per day, source precedence typed > imported > fetched; `latest(key) -> Observation | None`; `keys() -> list[SeriesKey]`.
    - `record_run(run: Run) -> None`; `last_run(platform, account) -> Run | None`; `last_ok(platform, account) -> datetime | None`; `stale_since(platform, account) -> date | None` (None when the last run succeeded; otherwise the UTC date of the last good run, or of the first failure if there never was one).
    - `kv_get(key: str) -> str | None`, `kv_set(key: str, value: str) -> None`.
    - `apply_click_batch(rows: Sequence[ClickDay], cursor: int) -> None` adds counts to `click_daily` and sets kv `worker_click_cursor` in one transaction; `click_cursor() -> int` (0 if unset); `clicks(since: date) -> list[ClickDay]`.
  - `open_store(config: StoreConfig, env: Mapping[str, str]) -> Store`; `SqliteStore(path: Path | ":memory:")` (creates parent directory, mode 0600 file, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`).
- Tables in `0001_core.sql`: `schema_version`, `observations` (PK on the five key columns, plus `fetched_at`, `access` with a CHECK, `expires_at` nullable, and an index on `expires_at`), `runs` (PK `platform, account, ts`), `kv`, `click_daily` (PK `day, link_id, country`).

- [ ] **Step 1: Write the contract tests** in `tests/store/contract.py` as `class StoreContract` (fixture `store` supplied by each backend's test module): `test_write_is_idempotent` (second `write` of the same rows returns `0`); `test_first_write_of_a_day_wins` (value 10 then 99 same key: `series` returns 10); `test_precedence_typed_over_imported_over_fetched`; `test_series_orders_by_day_and_honors_since`; `test_failed_run_is_stale_not_zero` (ok run on 2026-10-20, failed run on 2026-10-22: `stale_since == date(2026,10,20)` and `series` still has no zero row); `test_stale_since_none_after_recovery`; `test_never_ok_stale_since_is_first_failure_date`; `test_migrate_idempotent_and_returns_highest_version`; `test_newer_database_refused` (set `schema_version` to 9999: `migrate()` raises `StoreError`); `test_apply_click_batch_adds_counts_and_sets_cursor`; `test_apply_click_batch_atomic` (second row invalid, a `count=-1`: raises `ValueError`, nothing stored, cursor unchanged); `test_observation_rejects_nan_inf_negative_and_bad_slugs`; `test_expired_owner_row_purged_and_public_counts_survive` (an owner-authorized row with `expires_at = 2026-10-01` and a public row of the same age; `purge_expired(date(2026,10,23))` returns `1`, `series` keeps only the public row); `test_owner_row_without_expiry_is_kept`; `test_owner_row_not_yet_expired_is_kept`; `test_purge_never_touches_imported_or_typed_public_rows`; `test_origin_tag_for_each_source_and_access` (four values exactly as listed, imported and typed beat `access`); `test_public_observation_with_expiry_refused`. SQLite-only in `test_sqlite.py`: `test_file_mode_is_0600` (database file and its `-wal` and `-shm` files, parent directory 0700), `test_migration_files_applied_in_numeric_order_with_gaps` (temporary extra `0007_x.sql` is applied after `0001`).
- [ ] **Step 2: Run** `pytest tests/store -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `models.py`, `SqlStore` and `SqliteStore`; load migrations through `importlib.resources`. `series` resolves precedence in SQL with a window or a grouped `MIN` over a `CASE` rank, not in Python per row.
- [ ] **Step 4: Run** `pytest tests/store -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the append-only snapshot store (SQLite)`.

### Task 3: Postgres backend

**Files:**
- Create: `src/reach_around/store/postgres.py`, `tests/store/test_postgres.py`, `docs/education/snapshot-store.md`
- Modify: `src/reach_around/store/__init__.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `Db`, `SqlStore`, `StoreContract` (Task 2).
- Produces: `PostgresStore(dsn: str)`; `open_store` returns it for `kind == "postgres"` after reading `os.environ[config.dsn_env]`; the `?` placeholders are rewritten to `%s` inside the adapter only.

- [ ] **Step 1: Write failing tests**: `TestPostgres(StoreContract)` using fixture `store` that connects to `REACH_AROUND_TEST_POSTGRES_DSN` and skips when unset (each test in a fresh schema); `test_dsn_password_never_in_repr_or_error` (connect to a closed port with DSN `postgresql://u:s3cretpw@127.0.0.1:1/x`; `"s3cretpw"` is absent from `repr(store)` and from the raised exception text); `test_missing_dsn_env_raises_store_error_naming_the_variable`.
- [ ] **Step 2: Run** with the DSN set to a local container: `pytest tests/store/test_postgres.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the adapter using `psycopg`, importing it lazily so SQLite-only installs work, with the DSN scrubbed from every raised error. Add a repo-specific CI job `store-postgres` (a `postgres` service container pinned by digest, `pip install .[postgres]`, `pytest tests/store`), and add it to the `CI green` needs list. Write `docs/education/snapshot-store.md`: why the store is the history, append-only, precedence, partial days.
- [ ] **Step 4: Run** the suite against SQLite and Postgres. Expected: PASS on both.
- [ ] **Step 5: Commit** `feat: add the optional Postgres snapshot store`.

### Task 4: Secret backends and token registry

**Files:**
- Create: `src/reach_around/tokens/{__init__,secrets,registry}.py`, `src/reach_around/store/migrations/0002_tokens.sql`, `tests/tokens/test_secrets.py`, `tests/tokens/test_registry.py`, `tests/helpers.py` (`assert_secret_absent(sentinel: str, store: Store, *texts: object) -> None`)

**Interfaces:**
- Consumes: `Store`, `Store.db` (Task 2).
- Produces:
  - `class SecretMissing(KeyError)` (message is the name only); `class SecretWriteError(Exception)`.
  - `class SecretBackend(Protocol)`: `get(name: str) -> str`, `set(name: str, value: str) -> None`.
  - `EnvSecrets(env: Mapping[str, str])` (`set` raises `SecretWriteError`); `EncryptedFileSecrets(path: Path, key_env: str = "REACH_AROUND_SECRETS_KEY", env: Mapping[str, str] = os.environ)` using Fernet, atomic write (temp file, fsync, rename), mode 0600, read-back verified.
  - `@dataclass(frozen=True) TokenRecord(name: str, platform: str, account: str, expires_at: datetime | None, refreshable: bool, last_refreshed_at: datetime | None = None, needs_attention: bool = False)`.
  - `@dataclass(frozen=True) TokenAlert(name: str, platform: str, days_left: int, threshold: int | None, expired: bool)`.
  - `@dataclass(frozen=True) RefreshResult(secrets: Mapping[str, str], expires_at: datetime | None)`.
  - `TokenRegistry(store: Store, secrets: SecretBackend)`: `register(record: TokenRecord) -> None` (upsert; keeps alert state); `get(name) -> TokenRecord | None`; `all() -> list[TokenRecord]`; `expiring(now: datetime, warn_days: Sequence[int]) -> list[TokenAlert]`; `refresh(name: str, refresher: Callable[[], RefreshResult], now: datetime) -> None`.
- Migration `0002_tokens.sql`: `tokens(name PK, platform, account, expires_at, refreshable, last_refreshed_at, needs_attention, last_threshold, last_alert_day, lease_until)`. No secret value is ever a column.

- [ ] **Step 1: Write failing tests.** Secrets: `test_env_secrets_missing_name_message_is_the_name_only`; `test_env_secrets_set_raises`; `test_encrypted_file_roundtrip_and_mode_0600`; `test_encrypted_file_wrong_key_raises_without_leaking_contents`; `test_encrypted_file_write_is_atomic` (patch `os.replace` to raise: the old file content still reads back). Registry: `test_expiring_alerts_once_per_threshold` (expires in 13 days with `warn_days=[14,7,3,1]`: one alert with `threshold == 14`; same day again: `[]`; at 6 days left: one alert with `threshold == 7`); `test_expired_token_alerts_once_per_utc_day`; `test_no_expiry_never_alerts`; `test_refresh_persists_and_reads_back_before_registry_update` (record backend calls: every `set` precedes the registry write, and `get` equals the value set); `test_refresh_write_failure_flags_needs_attention_and_raises` (backend `set` raises: `SecretWriteError` propagates, `needs_attention` is true, `expires_at` unchanged); `test_concurrent_refresh_calls_refresher_once` (two threads released by a barrier; the refresher counter is `1`, both calls return, the second sees the new `expires_at`); `test_registry_rows_contain_no_secret_values` (dump the `tokens` table after a refresh with value `SENTINEL-VALUE`); `test_no_token_in_logs_exceptions_store_or_alerts` (after a successful refresh, a failed refresh and an expiry alert, `assert_secret_absent(sentinel, store, captured_logs, *exception_texts, *alert_messages)` from `tests/helpers.py` passes; it dumps every table through `store.db.query`; phase 4's export test must call the same helper); `test_secrets_file_and_directory_are_owner_only` (file mode 0600, directory 0700, including when the file already exists with mode 0644, which is tightened); `test_tokens_table_lives_in_the_owner_only_store_file` (the SQLite file holding the `tokens` table is 0600, shared with Task 2's check).
- [ ] **Step 2: Run** `pytest tests/tokens -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The lease is a conditional `UPDATE tokens SET lease_until = ? WHERE name = ? AND (lease_until IS NULL OR lease_until < ?)`; a caller that loses the lease waits up to 30 seconds for it, re-reads the record and returns without calling the refresher if it is already fresh.
- [ ] **Step 4: Run** `pytest tests/tokens -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add secret backends and the token registry`.

### Task 5: Alert notifiers and token health check

**Files:**
- Create: `src/reach_around/tokens/notify.py`, `tests/tokens/test_notify.py`, `docs/education/token-health.md`

**Interfaces:**
- Consumes: `TokenRegistry`, `TokenAlert` (Task 4); `Alerts`, `AlertDestination` (Task 1); `httpx.Client`.
- Produces:
  - `@dataclass(frozen=True) Alert(kind: Literal["token","stale","pull","listener"], subject: str, message: str)`.
  - `class Notifier(Protocol)`: `send(alert: Alert) -> None`.
  - `StdoutNotifier()`; `DiscordWebhookNotifier(url: str, http: httpx.Client)` (https only); `MatrixNotifier(homeserver: str, room_id: str, access_token: str, http: httpx.Client)`.
  - `build_notifiers(alerts: Alerts, secrets: SecretBackend, http: httpx.Client) -> list[Notifier]`.
  - `dispatch(notifiers: Sequence[Notifier], alert: Alert) -> int` returns the number delivered; never raises.
  - `check_token_health(registry: TokenRegistry, notifiers: Sequence[Notifier], now: datetime, warn_days: Sequence[int]) -> int` returns alerts sent.

- [ ] **Step 1: Write failing tests**: `test_discord_payload_disables_mentions` (body JSON has `allowed_mentions == {"parse": []}` even when the subject contains `@everyone`); `test_discord_refuses_http_url`; `test_matrix_send_uses_deterministic_txn_id` (same alert twice the same day: same transaction id in the PUT path, so the server de-duplicates); `test_failed_notifier_does_not_stop_the_others` (first raises `httpx.ConnectError`; second still receives; `dispatch` returns `1`); `test_webhook_url_and_matrix_token_never_logged_on_failure` (sentinel values absent from `captured_logs`); `test_token_alert_message_has_names_and_numbers_only` (message equals `"token twitch-main expires in 7 days"`); `test_check_token_health_sends_once_per_threshold`.
- [ ] **Step 2: Run** `pytest tests/tokens/test_notify.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the notifiers and `check_token_health`; Matrix path is `PUT /_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}` with the room id URL-encoded. Write `docs/education/token-health.md`: expiry schedules from the research notes, what the registry can and cannot refresh, why alerts carry names and never values.
- [ ] **Step 4: Run** `pytest tests/tokens -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: alert the owner before tokens lapse`.

### Task 6: Collector framework

**Files:**
- Create: `src/reach_around/collectors/{__init__,base,http,runner}.py`, `src/reach_around/commands/{__init__,collect}.py`, `src/reach_around/collectors/platforms/__init__.py`, `tests/collectors/{conftest,test_http,test_runner,test_base}.py`
- Modify: `src/reach_around/cli.py` (import every module in `reach_around.commands`, call `register(subparsers)`)

**Interfaces:**
- Consumes: `Account`, `SiteConfig` (Task 1); `Store`, `Observation`, `Run` (Task 2); `SecretBackend`, `TokenRegistry` (Task 4).
- Produces:
  - `class CollectorError(Exception)` (`reason` is safe text); `class AuthExpired(CollectorError)`; `class HttpError(CollectorError)` with `status: int`.
  - `@dataclass CollectorContext(http: httpx.Client, secrets: SecretBackend, registry: TokenRegistry, today: date, tz: ZoneInfo, now: datetime, cache: dict[str, object])` (`cache` is per run, in memory, never persisted).
  - `class Collector(Protocol)`: `platform: ClassVar[str]`; `metrics: ClassVar[frozenset[str]]`; `owner_metrics: ClassVar[frozenset[str]]` (subset of `metrics` read through owner authorization, default empty); `owner_retention_days: ClassVar[int | None]` (None = no platform limit); `collect(self, account: Account, ctx: CollectorContext) -> list[Observation]`.
  - `register(cls) -> cls` decorator; `get_collector(platform: str) -> Collector`; `discover() -> None`.
  - `require_count(payload: Mapping[str, object], field: str) -> int` raises `CollectorError("missing or invalid field: <field>")` for a missing key, a bool, a float, a string or a negative number; never includes the value.
  - `build_client(*, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> httpx.Client` (`follow_redirects=False`, `trust_env=False`, user agent `reach-around/<version>`).
  - `request_json(client, method: str, url: str, *, headers: Mapping[str, str] | None = None, params: Mapping[str, str] | None = None, json: object | None = None, idempotent: bool = True, retries: int = 3, max_bytes: int = 5_000_000, sanitize: Callable[[object], object] | None = None, sleep: Callable[[float], None] = time.sleep) -> object`.
  - `redact(text: str, secret_values: Iterable[str]) -> str`; `configure_logging(secret_values: Iterable[str]) -> None` (installs a masking filter, sets `httpx` and `httpcore` to WARNING).
  - `@dataclass(frozen=True) RunOutcome(platform: str, account: str, ok: bool, inserted: int, reason: str | None)`; `collect_all(config: SiteConfig, store: Store, ctx: CollectorContext, *, platforms: Collection[str] | None = None) -> list[RunOutcome]`.
  - CLI `reach-around collect --config PATH [--platform NAME ...]`; exit `0` all ok, `1` any account failed, `2` config error.

- [ ] **Step 1: Write failing tests.** HTTP: `test_get_retries_429_with_retry_after_capped_at_60s` (fake sleep records `[3.0]` for `Retry-After: 3` and `[60.0]` for `9999`); `test_get_retries_5xx_three_times_then_raises_http_error`; `test_post_is_never_retried_after_5xx_or_timeout` (`idempotent=False`: exactly one request); `test_redirect_is_an_error_and_authorization_is_not_forwarded` (302 to another host: `HttpError`, the second request is never made); `test_error_message_has_status_and_path_but_no_query_or_body` (body and `?token=SENTINEL` absent from `str(error)`); `test_oversized_body_refused` (6 MB); `test_sanitize_runs_before_return_and_before_any_error_path`. Base: `test_require_count_accepts_int_only` (`120` ok; `"120"`, `12.5`, `True`, `-1`, missing all raise, and `"SENTINEL"` as a value is not in the message); `test_discover_registers_every_platform_module`; `test_observation_metric_outside_declared_metrics_is_a_failure`; `test_runner_stamps_owner_metrics_with_access_and_expiry` (a test collector with `owner_metrics={"x"}`, `owner_retention_days=30`: its `x` rows are stored `OWNER` with `expires_at == day + 30`, its public metrics stay `PUBLIC` with none; with `owner_retention_days=None` the `expires_at` is `None`); `test_collector_cannot_declare_owner_metric_outside_metrics`. Runner: `test_success_writes_observations_and_records_ok_run`; `test_exception_records_failed_run_and_writes_nothing` (exit path: `stale_since` is the earlier ok date, `series` has no new row, no zero row); `test_empty_observation_list_is_a_failure`; `test_one_failing_account_does_not_stop_others`; `test_failure_reason_is_redacted_and_truncated_to_200_chars` (secret value inside the exception text is replaced by `[redacted]`); `test_unknown_platform_in_config_fails_before_any_collection` (exit `2`, nothing recorded); `test_logging_filter_masks_secret_values`; `test_collect_command_exit_codes`.
- [ ] **Step 2: Run** `pytest tests/collectors -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules. In `collect_all`, resolve each account's secret names through `ctx.secrets` only inside the collector, and build the redaction set from every secret the accounts reference plus their PEM lines.
- [ ] **Step 4: Run** `pytest tests/collectors tests/test_cli.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the collector framework and collect command`.

### Task 7: Scheduler and stale alerts

**Files:**
- Create: `src/reach_around/commands/schedule.py`, `src/reach_around/collectors/schedule.py`, `tests/collectors/test_schedule.py`, `docs/numbers.md` (run-location section)

**Interfaces:**
- Consumes: `collect_all`, `build_notifiers`, `check_token_health`, `Store.stale_since`, `Store.kv_get/kv_set`.
- Produces: each scheduled run calls `store.purge_expired(today)` after collecting; CLI `reach-around purge --config PATH` does it once and prints the count. `next_run(now: datetime, at: time, tz: ZoneInfo) -> datetime`; `check_stale(store, accounts: Sequence[Account], notifiers, today: date, stale_days: int) -> int`; `run_schedule(config, store, ctx_factory: Callable[[], CollectorContext], stop: threading.Event, *, now: Callable[[], datetime] = ...) -> None`; CLI `reach-around schedule --config PATH [--at HH:MM] [--once]` (default `05:30` in the config time zone).

- [ ] **Step 1: Write failing tests**: `test_next_run_later_today_and_tomorrow` (at 05:30 America/New_York; now `2026-10-23T08:00Z` gives `2026-10-23T09:30Z`; now `2026-10-23T12:00Z` gives `2026-10-24T09:30Z`); `test_next_run_across_spring_forward` (at 02:30, now `2027-03-13T12:00Z`: result `2027-03-14T07:30Z`); `test_next_run_across_fall_back_runs_once` (at 01:30 on `2026-11-01`: exactly one run between `2026-11-01T04:00Z` and `2026-11-02T04:00Z`); `test_check_stale_alerts_at_ten_days_once_per_day` (accounts stale since 2026-10-13: one alert on 2026-10-23, none on a second call the same day, one the next day); `test_stale_alert_message_has_names_and_dates_only`; `test_a_crashing_run_does_not_end_the_loop` (first `collect_all` raises; the loop runs again); `test_stop_event_ends_loop_within_one_second`; `test_once_runs_collectors_then_health_checks_and_exits`; `test_scheduled_run_purges_expired_owner_rows_and_keeps_public_counts` (end to end through `run_schedule --once` with a store holding one expired owner row and one old public row); `test_purge_command_prints_count`.
- [ ] **Step 2: Run** `pytest tests/collectors/test_schedule.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with `zoneinfo`; the loop waits with `stop.wait(timeout)`; dedupe stale alerts through kv key `alerted:stale:<platform>:<account>:<utc-date>`. In `docs/numbers.md` state which collectors may run in a scheduled CI job (public endpoints or plain keys, and only against a Postgres store reached through `dsn_env`) and which belong in the homelab container (rotating refresh tokens: Matrix, Twitch).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the daily scheduler and stale alerts`.

### Task 8: Bluesky collector

**Files:**
- Create: `src/reach_around/collectors/platforms/bluesky.py`, `tests/collectors/test_bluesky.py`, `docs/collectors/bluesky.md`

**Interfaces:**
- Consumes: `Collector`, `require_count`, `request_json` (Task 6).
- Produces: `@register class BlueskyCollector`: `platform = "bluesky"`, `metrics = {"followers", "following", "posts"}`; options `handle: str` (a handle or a `did:` id, validated). Public endpoint `GET https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=<handle>`, no secrets.

- [ ] **Step 1: Write failing tests** with fixture `{"did": "did:plc:example", "handle": "example.bsky.social", "followersCount": 120, "followsCount": 45, "postsCount": 300}`: `test_three_observations_for_today` (values 120, 45, 300, `day == ctx.today`, `source == FETCHED`); `test_missing_followers_count_raises_not_zero` (`CollectorError` mentioning `followersCount`, nothing returned); `test_string_count_refused`; `test_http_500_raises_http_error`; `test_handle_with_slash_or_space_refused`; live test `test_live_profile_fields` (marked `live`, skipped unless `--run-live`, handle `bsky.app`, asserts the three fields are ints; this retires the UNVERIFIED field-name note).
- [ ] **Step 2: Run** `pytest tests/collectors/test_bluesky.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `collect`; add the `--run-live` flag and `live` marker to `conftest.py` if absent. Document setup in `docs/collectors/bluesky.md` (no key, counts and URIs only, no analytics exist).
- [ ] **Step 4: Run** the tests. Expected: PASS (live test skipped).
- [ ] **Step 5: Commit** `feat: add the Bluesky collector`.

### Task 9: Mastodon collector

**Files:**
- Create: `src/reach_around/collectors/platforms/mastodon.py`, `tests/collectors/test_mastodon.py`, `docs/collectors/mastodon.md`

**Interfaces:**
- Produces: `@register class MastodonCollector`: `platform = "mastodon"`, `metrics = {"followers", "following", "posts"}`; options `instance: str` (bare hostname), `handle: str`; optional secret `access_token`. Calls `GET https://{instance}/api/v1/accounts/lookup?acct={handle}` and reads `followers_count`, `following_count`, `statuses_count`.

- [ ] **Step 1: Write failing tests**: `test_counts_from_lookup`; `test_instance_must_be_a_bare_public_hostname` (each of `example.social/evil`, `user@example.social`, `example.social:8080`, `127.0.0.1`, `localhost`, `10.0.0.5` raises `ConfigError`); `test_token_sent_as_bearer_only_when_configured`; `test_401_says_a_token_is_needed_and_is_not_zero`; `test_missing_followers_count_raises` (Pixelfed-style payload without the field); `test_redirect_to_other_host_is_refused_and_token_not_forwarded`; live test `test_live_lookup` against a public instance (`--run-live`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Note in the docs that only Mastodon is promised; Pixelfed parity is unverified and an instance without the fields fails cleanly.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Mastodon collector`.

### Task 10: GitHub collector (GitHub App)

**Files:**
- Create: `src/reach_around/collectors/platforms/github.py`, `tests/collectors/test_github.py`, `docs/collectors/github.md`

**Interfaces:**
- Produces: `mint_app_jwt(app_id: str, private_key_pem: str, now: datetime) -> str` (RS256; claims `iat = now - 60 s`, `exp = now + 540 s`, `iss = app_id`); `@register class GithubCollector`: `platform = "github"`, `metrics = {"followers", "stars", "forks", "views", "views_unique", "clones", "clones_unique"}`, `owner_metrics = {"views", "views_unique", "clones", "clones_unique"}` (repo traffic is administrator-only data), `owner_retention_days = None`. Options: `kind: "user" | "repo"`, `login` (user) or `repo` (`owner/name`), `traffic: bool = True`. Secrets: `app_id`, `installation_id`, `private_key`. User units write `followers` from `GET /users/{login}`; repo units write `stars` and `forks` from `GET /repos/{r}` for today, and, with `traffic`, per-day `views`, `views_unique`, `clones`, `clones_unique` from `/traffic/views` and `/traffic/clones` (`per=day`).

- [ ] **Step 1: Write failing tests** (a test RSA key is generated in a fixture): `test_jwt_claims` (decode without verification: `exp - now == 540`, `iat == now - 60`, `iss == "12345"`); `test_installation_token_is_requested_once_per_run_and_not_retried` (two units, one POST to `/app/installations/{id}/access_tokens`; a 502 on it raises with exactly one request); `test_installation_token_never_stored_or_logged` (sentinel in the token response is absent from every table and `captured_logs`); `test_stars_and_forks_for_today`; `test_traffic_writes_only_complete_utc_days` (response includes 2026-10-22 and 2026-10-23 with `today = 2026-10-23`: only 2026-10-22 is written); `test_traffic_days_come_from_response_timestamps` (14 complete days gives 56 observations dated by their own timestamps); `test_rerun_with_same_response_inserts_nothing` (through the store: second `write` returns `0`); `test_traffic_403_fails_the_unit_with_a_clear_reason` (reason mentions the Administration permission; no stars written for that unit); `test_user_followers_missing_field_raises`; `test_repo_option_must_match_owner_slash_name` (`../x` refused); live test `test_live_public_repo_counts` using the unauthenticated endpoints.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** with `PyJWT`. Cache the installation token in `ctx.cache` only. Accept the PEM with literal `\n` sequences from an environment variable. Document the GitHub App setup (permissions Metadata and Administration read, Followers read for the user unit), the 14-day traffic window and the 10-day missed-run alert from Task 7. Note that days GitHub omits are left as gaps, not zeros.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the GitHub App collector`.

### Task 11: Discord collector

**Files:**
- Create: `src/reach_around/collectors/platforms/discord.py`, `tests/collectors/test_discord.py`, `docs/collectors/discord.md`

**Interfaces:**
- Produces: `@register class DiscordCollector`: `platform = "discord"`, `metrics = {"members", "members_online"}` (`members_online` is Discord's non-offline presence count; the docs say so). Options `mode: "bot" | "invite" = "bot"`, `guild_id` (digits) or `invite_code`. Secret `bot_token` (bot mode). Bot mode: `GET https://discord.com/api/v10/guilds/{guild_id}?with_counts=true` with `Authorization: Bot <token>`, fields `approximate_member_count`, `approximate_presence_count`. Invite mode: `GET /invites/{code}?with_counts=true`, no Authorization header.

- [ ] **Step 1: Write failing tests**: `test_bot_mode_counts_and_header`; `test_invite_mode_sends_no_authorization_header`; `test_guild_id_must_be_digits` (`../users/@me` refused); `test_invite_code_must_be_alphanumeric`; `test_401_raises_auth_expired_naming_the_bot_token_not_its_value`; `test_missing_member_count_raises_not_zero`; `test_bot_token_never_logged`; live test `test_live_invite_counts` with a public invite (`--run-live`; records whether the endpoint needs auth, retiring that UNVERIFIED item).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Docs: bot with no permissions, all privileged intents off, net daily change is derived by the dashboard from snapshots, no join/leave history exists.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Discord collector`.

### Task 12: Matrix collector

**Files:**
- Create: `src/reach_around/collectors/platforms/matrix.py`, `tests/collectors/test_matrix.py`, `docs/collectors/matrix.md`

**Interfaces:**
- Consumes: `TokenRegistry.refresh`, `RefreshResult` (Task 4).
- Produces: `@register class MatrixCollector`: `platform = "matrix"`, `metrics = {"members"}`. Options `homeserver` (https base URL), `room_id`, `exclude_user_ids: list[str] = []`. Secrets `access_token`, optional `refresh_token`. Calls `GET {homeserver}/_matrix/client/v3/rooms/{room_id}/joined_members` and counts the `joined` map minus any `exclude_user_ids` present in it. Token registry name `matrix-<account.id>`.

- [ ] **Step 1: Write failing tests**: `test_room_id_is_url_encoded` (`!abc:example.org` requested as `%21abc%3Aexample.org`); `test_members_counts_joined_minus_excluded`; `test_homeserver_must_be_https`; `test_empty_joined_map_is_refused_not_zero` (reason says the monitoring account is not in the room); `test_403_forbidden_is_a_failure`; `test_unknown_token_without_refresh_token_raises_auth_expired`; `test_refresh_persists_new_tokens_before_retrying` (backend call log: `set(refresh_token)`, `set(access_token)`, then the retried request; the retry uses the new access token); `test_refresh_write_failure_raises_and_does_not_retry_with_the_unsaved_token` (Review Focus 3); `test_only_one_retry_after_refresh` (second `M_UNKNOWN_TOKEN` raises `AuthExpired`).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the refresh through `ctx.registry.refresh(name, refresher, ctx.now)` with `POST /_matrix/client/v3/refresh` (`idempotent=False`). Docs: dedicated read-only account, it counts itself, tokens may expire, MAS behavior is unverified.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Matrix collector`.

### Task 13: Twitch collector (counts and VODs)

**Files:**
- Create: `src/reach_around/collectors/twitch_auth.py`, `src/reach_around/collectors/platforms/twitch.py`, `tests/collectors/test_twitch.py`, `docs/collectors/twitch.md`

**Interfaces:**
- Consumes: `TokenRegistry.refresh` (Task 4).
- Produces:
  - `app_token(ctx: CollectorContext, client_id: str, client_secret: str) -> str` (client credentials, cached in `ctx.cache`, not refreshable); `user_access_token(ctx, account: Account, client_id: str, client_secret: str) -> str` (refresh-token grant, persists any returned refresh token through the registry before returning the access token).
  - `parse_twitch_duration(text: str) -> int` seconds (`"1h30m0s"` is 5400, `"45s"` is 45); raises `CollectorError` for anything else.
  - `@register class TwitchCollector`: `platform = "twitch"`, `metrics = {"followers", "subscribers", "vod_count", "vod_views_total", "stream_count", "stream_minutes"}`. Options `login`. Secrets `client_id`, `client_secret`, `refresh_token`. `followers` from `GET /helix/channels/followers?broadcaster_id=` (`total`), `subscribers` from `GET /helix/subscriptions?broadcaster_id=` (`total`), `vod_count` and `vod_views_total` for today from `GET /helix/videos?user_id=&type=archive&first=100`; `stream_count` and `stream_minutes` per completed day (before `ctx.today` in `ctx.tz`) from the same VODs, excluding any VOD whose `stream_id` matches the stream returned by `GET /helix/streams?user_id=` (a live stream). `owner_metrics = {"subscribers"}`, `owner_retention_days = None` (the Developer Agreement storage question stays open; setting a number later is one line).

- [ ] **Step 1: Write failing tests**: `test_refresh_token_persisted_before_first_helix_call` (call log order); `test_followers_total_field` (payload `{"total": 321, "data": [], "pagination": {}}`); `test_subscribers_total_field`; `test_missing_total_raises_not_zero`; `test_parse_twitch_duration_cases` (`"1h30m0s"`, `"45m10s"`, `"45s"`, `"2h0m5s"` accepted; `""`, `"abc"`, `"-1s"` raise); `test_vod_day_totals_use_tz_date_and_only_completed_days` (three VODs: two on 2026-10-22 and one on 2026-10-23 with `today = 2026-10-23`; `stream_count`/`stream_minutes` written for 10-22 only, summing both); `test_in_progress_stream_vod_excluded`; `test_vod_count_and_views_total_are_for_today`; `test_401_after_refresh_raises_auth_expired`; `test_user_scope_missing_subscribers_403_fails_unit_with_scope_name`; live test `test_live_app_token_streams_shape` (needs client credentials; records the Get Streams and subscriber response shapes, retiring those UNVERIFIED items).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Register the user token in the registry as `twitch-<account.id>` (`refreshable=True`, no expiry). Docs: required scopes `moderator:read:followers channel:read:subscriptions`, the Developer Agreement storage question stays open (the maintainer reads it), VOD retention depends on account type.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Twitch collector for counts and VODs`.

### Task 14: Kick collector (strip the stream key)

**Files:**
- Create: `src/reach_around/collectors/platforms/kick.py`, `tests/collectors/test_kick.py`, `docs/collectors/kick.md`

**Interfaces:**
- Produces: `drop_stream_key(payload: object) -> object` (removes `stream.key` at every depth it appears, returns a new object); `@dataclass(frozen=True) ChannelSnapshot(slug: str, subscribers: int, subscribers_gifted: int)` (no key field); `@register class KickCollector`: `platform = "kick"`, `metrics = {"subscribers", "subscribers_gifted"}`, `owner_metrics = {"subscribers", "subscribers_gifted"}`, `owner_retention_days = None`. Options `slug`. Secrets `client_id`, `client_secret`. App token from `POST https://id.kick.com/oauth/token` (`grant_type=client_credentials`, `idempotent=False`), then `GET https://api.kick.com/public/v1/channels?slug=<slug>` with `sanitize=drop_stream_key`. No follower metric exists (the API has none); followers are entered through Task 15.

- [ ] **Step 1: Write failing tests** with a channel fixture whose `stream.key` is `"SENTINEL-STREAM-KEY"` (response shape from `docs/research/kick.md`): `test_stream_key_never_reaches_logs_store_or_errors` (run at DEBUG with `captured_logs`; afterwards the sentinel is absent from every log record, every table of the store (dump all with `store.db.query`), the `RunOutcome.reason`, and `repr`/`str` of every exception raised in a second run where the channel body is malformed); `test_snapshot_dataclass_has_no_key_field` (`"key" not in {f.name for f in dataclasses.fields(ChannelSnapshot)}`); `test_drop_stream_key_removes_it_at_depth_and_keeps_other_fields`; `test_error_body_not_in_message` (500 whose body contains the sentinel); `test_subscriber_counts_from_active_fields`; `test_missing_active_subscribers_count_raises_not_zero`; `test_followers_are_not_a_collected_metric` (`"followers" not in KickCollector.metrics`); `test_token_response_secret_not_logged`; live test `test_live_channel_shape_with_app_token` (`--run-live`, requires credentials; asserts the subscriber fields exist under an app token and that `stream.key` is present only to confirm the strip is needed).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** If the live test shows the subscriber fields absent under an app token, stop and file an issue for a user-token mode on the Task 4 refresher; do not guess. Docs: 2FA needed, Developer Terms still unread (open item), followers by manual or CSV entry, webhooks out of scope.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Kick collector with stream key stripping`.

### Task 15: Manual and CSV/XLSX import

**Files:**
- Create: `src/reach_around/imports.py`, `src/reach_around/commands/{import_cmd,set_cmd}.py`, `tests/test_imports.py`, `docs/collectors/manual-import.md`

**Interfaces:**
- Consumes: `Store`, `Observation`, `Source` (Task 2).
- Produces: `class ImportRejected(ConfigError)` carrying `rows: list[tuple[int, str]]` (row number, problem); `read_rows(path: Path, *, max_bytes: int = 5_000_000, max_rows: int = 50_000, max_uncompressed: int = 50_000_000) -> list[tuple[str, str, str, date, float]]`; `import_file(store: Store, path: Path, *, today: date) -> int` (rows inserted, source `IMPORTED`); `set_value(store: Store, platform: str, account: str, metric: str, value: float, day: date, *, today: date) -> bool` (source `TYPED`). CLI `reach-around import PATH --config PATH` and `reach-around set PLATFORM ACCOUNT METRIC VALUE [--day YYYY-MM-DD] --config PATH`. Header row is exactly `platform,account,metric,day,value`; any platform slug is allowed (LinkedIn and TikTok have no collector).

- [ ] **Step 1: Write failing tests** (files built in `tmp_path`): `test_csv_import_is_flagged_imported` (`series` source is `IMPORTED`); `test_set_is_flagged_typed`; `test_imported_loses_to_typed_wins_over_fetched_on_read`; `test_any_bad_row_rejects_whole_file` (999 good rows and one bad: `ImportRejected`, store unchanged, `rows == [(502, ...)]`); `test_nan_inf_negative_refused` (`nan`, `inf`, `-1`); `test_future_day_refused`; `test_duplicate_rows_in_file_refused`; `test_wrong_header_refused`; `test_xlsx_roundtrip_with_date_cells`; `test_xlsx_formula_without_cached_value_refused` (openpyxl-written `=1+1` is read with `data_only=True` and arrives empty: refused as an empty value); `test_xlsm_extension_refused`; `test_oversized_file_refused`; `test_xlsx_zip_bomb_refused` (a 60 MB-uncompressed zip of zeros smaller than 1 MB on disk is refused before parsing); `test_row_cap_refused` (50,001 rows); `test_symlink_refused`; `test_fifo_refused` (`os.mkfifo`; the call returns promptly instead of blocking); `test_import_accepts_platform_without_a_collector` (`linkedin`).
- [ ] **Step 2: Run** `pytest tests/test_imports.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Open with `os.open(..., O_RDONLY | O_NOFOLLOW | O_NONBLOCK)`, `fstat` must be a regular file, check size, sum `zipfile.ZipFile.infolist()` `file_size` before handing to `openpyxl.load_workbook(read_only=True, data_only=True)`. Collect every row problem before raising. Docs: the CSV layout, how Kick followers and LinkedIn numbers are entered, and that the media kit (phase 3) can mark these as self-reported.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add manual, CSV and XLSX import`.

### Task 16S: Spike: can a read-only token run the aggregate query?

Unverified: whether a Cloudflare API token limited to reading D1 can run the grouped `SELECT` through the D1 query endpoint (the endpoint may demand a broader permission, which would make the collector host's token able to write or read leads). This spike answers it before Task 16 is built, with a defined fallback.

**Files:**
- Create: `docs/research/d1-read-token.md`

- [ ] **Step 1: Prepare** a throwaway D1 database (never the production one) with the Plan 1 `click_events` table, a stand-in `leads(email)` table holding synthetic addresses at `example.com`, and a scoped API token whose only permission is the lowest D1 read permission the dashboard offers.
- [ ] **Step 2: Try** with that token, via `curl` against `POST /client/v4/accounts/{account_id}/d1/database/{database_id}/query`: (a) the Task 16 aggregate query (`GROUP BY` on 15-minute buckets, `COUNT(*)`); (b) `SELECT email FROM leads`; (c) an `INSERT`. Record the HTTP status and the permission name Cloudflare reports for each, without pasting the token.
- [ ] **Step 3: Decide and record** in `docs/research/d1-read-token.md` with the date and the permission names. Rule: the `d1` transport is accepted only if (a) succeeds and (c) fails. If (b) also succeeds, say so plainly: the token can read leads, so it must be held only on the owner's collector host and the `signed_worker` fallback is preferred. If (a) fails or (c) succeeds, Task 16 defaults `worker_events.transport` to `signed_worker` and Task 16F is built.
- [ ] **Step 4: Commit** `docs: record whether a read-only D1 token can aggregate`.

### Task 16: Daily pull of Worker click aggregates

**Files:**
- Create: `src/reach_around/events/{__init__,source,cloudflare,pull}.py`, `src/reach_around/commands/pull_events.py`, `tests/events/{test_cloudflare,test_pull}.py`, `docs/education/system-of-record.md`

**Interfaces:**
- Consumes: `request_json` (Task 6), `Store.apply_click_batch`, `Store.click_cursor` (Task 2), `WorkerEvents` (Task 1), the Worker table from Plan 1 Task 8, the outcome of Task 16S.
- Produces:
  - `@dataclass(frozen=True) ClickBucket(bucket_ms: int, link_id: str, country: str | None, count: int)` where `bucket_ms` is the start of a 15-minute UTC bucket (15 minutes divides every real-world UTC offset, so a bucket never straddles a local midnight).
  - `class AggregateSource(Protocol)`: `max_id() -> int`; `buckets(after_id: int, upto_id: int) -> list[ClickBucket]`. Only grouped counts cross this boundary: no event id, no row, no other table.
  - `D1Client(account_id: str, database_id: str, token: str, http: httpx.Client)` implementing it with `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query`, `idempotent=True`, token only in the `Authorization` header. The two statements are fixed: `SELECT COALESCE(MAX(id), 0) AS max_id FROM click_events` and `SELECT (ts / 900000) * 900000 AS bucket_ms, link_id, country, COUNT(*) AS n FROM click_events WHERE id > ? AND id <= ? GROUP BY bucket_ms, link_id, country`.
  - `to_click_days(buckets: Sequence[ClickBucket], tz: ZoneInfo) -> tuple[list[ClickDay], int]` (rows and skipped count; skips a `link_id` that is not a slug, a non-positive count; country not two uppercase letters becomes `""`).
  - `pull_clicks(store: Store, source: AggregateSource, tz: ZoneInfo, *, window: int = 50_000) -> PullResult(clicks: int, skipped: int, cursor: int, source_behind_cursor: bool)`: reads `upto = source.max_id()`, then for each disjoint id window `(lo, hi]` up to `upto` fetches buckets and calls `store.apply_click_batch(rows, cursor=hi)`. Records a run for platform `reach-around-worker`, account `clicks`. CLI `reach-around pull-events --config PATH`; the transport comes from `worker_events.transport`.

- [ ] **Step 1: Write failing tests**: `test_buckets_convert_to_config_timezone_days` (bucket for `2027-03-14T04:30:00Z` counts on 2027-03-13, `2027-03-14T05:30:00Z` on 2027-03-14); `test_unknown_or_invalid_country_stored_as_empty_string`; `test_invalid_link_id_skipped_and_counted`; `test_second_pull_with_no_new_events_adds_nothing`; `test_window_failure_midway_never_double_counts` (`window=1000`, 2,500 ids; the source raises `HttpError` on the second window; counts equal window one only and the cursor equals 1000; the retry pulls windows two and three; final totals equal the sum exactly once); `test_source_behind_cursor_is_reported_and_not_reset` (`max_id() < cursor`: `source_behind_cursor` true, an alert is dispatched, cursor unchanged); `test_pull_requests_only_aggregates` (every statement sent by `D1Client` is one of the two fixed statements, contains `GROUP BY` or `MAX(`, and none selects `email` or names `leads`); `test_sql_is_parameterized` (ids appear only in `params`); `test_pull_never_writes_lead_emails_into_aggregate_tables` (a hostile source returns buckets whose `link_id` and `country` are `"person@example.com"`, and a D1 response with an extra `email` column; afterwards `click_daily` and `kv` hold no `@`, the buckets are counted as skipped, and `assert_secret_absent("person@example.com", store, captured_logs)` passes); `test_d1_client_reads_only_named_columns` (extra columns in the response are ignored and never stored); `test_token_only_in_authorization_header_and_never_logged`; `test_failed_pull_records_stale_run_and_changes_nothing`.
- [ ] **Step 2: Run** `pytest tests/events -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules. `docs/education/system-of-record.md`: why the local store is the record, the 90-day Worker retention, why only grouped counts leave the Worker's database, why leads never travel through this path, and the Task 16S result.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: pull Worker click aggregates into the store`.

### Task 16F: Fallback: signed owner aggregation endpoint (build only if Task 16S says so)

This adds a read route to the Worker, which the spec's section 4.4 forbids ("never returns visitor data"). It returns grouped click counts only, and only to a signed owner request. It needs the maintainer's approval before it is built, because it amends the spec; ask in the task's issue.

**Files:**
- Create: `worker/src/owner_clicks.ts`, `worker/test/owner_clicks.test.ts`, `src/reach_around/events/signed_worker.py`, `tests/events/test_signed_worker.py`
- Modify: `worker/src/index.ts` (route `GET /o/clicks`), `worker/wrangler.toml` (secret binding `OWNER_SIGNING_KEY`, no value)

**Interfaces:**
- Consumes: `AggregateSource`, `ClickBucket` (Task 16), `click_events` (Plan 1 Task 8).
- Produces: Worker `GET /o/clicks?after=<id>&upto=<id>` returning `{"max_id": n, "buckets": [{"bucket_ms", "link_id", "country", "count"}]}`; request headers `X-RA-Timestamp` (epoch seconds), `X-RA-Nonce`, `X-RA-Signature` = hex HMAC-SHA256 over `GET\n/o/clicks\n<sorted query>\n<timestamp>\n<nonce>` with `OWNER_SIGNING_KEY`. `SignedWorkerClient(worker_url: str, signing_key: str, http: httpx.Client)` implementing `AggregateSource`.

- [ ] **Step 1: Write failing tests**: Worker: `unsigned request gets 404 and an empty body` (indistinguishable from an unknown route); `wrong signature, tampered query, stale timestamp (more than 60 s) and reused nonce each get the same 404`; `valid request returns grouped buckets only` (no `id`, no per-event field, no `email`, no table other than `click_events` touched); `response for a signed request never includes lead data` (a `leads` row exists in the test database; its email is absent from the body). Python: `test_signature_covers_path_query_timestamp_and_nonce`; `test_client_implements_aggregate_source_and_pull_clicks_works_with_it`; `test_signing_key_never_logged`.
- [ ] **Step 2: Run** `npm test` in `worker/` and `pytest tests/events/test_signed_worker.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with constant-time signature comparison and a nonce table in D1 pruned after 5 minutes. Document the amendment in `docs/education/system-of-record.md`.
- [ ] **Step 4: Run** both suites. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add a signed owner aggregation route (fallback)`.

### Task 17: Built-in dashboard

**Files:**
- Create: `src/reach_around/dashboard/{__init__,build}.py`, `src/reach_around/dashboard/templates/index.html`, `src/reach_around/commands/dashboard.py`, `tests/dashboard/test_build.py`, `docs/dashboard.md`

**Interfaces:**
- Consumes: `Store` (Task 2), `TokenRegistry` (Task 4).
- Produces: `change(series: Sequence[Observation], days: int, today: date) -> float | None` (latest value minus the value exactly `days` before `today`; `None` if either is missing); `sparkline_path(points: Sequence[tuple[date, float]], width: int, height: int) -> str` (SVG path with a new `M` after any gap of more than one day); `build_dashboard(store: Store, out_dir: Path, *, now: datetime, tz: ZoneInfo, registry: TokenRegistry | None = None) -> Path` (writes `index.html` and returns it). CLI `reach-around dashboard --config PATH --out DIR`. One self-contained HTML file, no JavaScript, inline CSS and SVG, `<meta name="robots" content="noindex">`.

- [ ] **Step 1: Write failing tests**: `test_change_is_none_when_baseline_day_missing`; `test_change_computes_difference` (100 on 2026-10-16, 130 on 2026-10-23: `change(..., 7, ...) == 30.0`); `test_sparkline_breaks_at_gaps` (days 1, 2, 5: exactly two `M` commands); `test_failed_collector_shows_stale_since_and_last_value_not_zero` (the page text contains `stale since 2026-10-20` and the last real value `1,234`, and no cell shows `0` for that series); `test_never_collected_account_shows_never_collected`; `test_imported_and_typed_values_carry_visible_text_badges` (`imported`, `typed`, not color alone); `test_origin_shown_as_text_for_all_four_origins` (`public_api`, `owner_authorized_api`, `imported`, `typed`); `test_account_names_are_html_escaped` (`<img src=x onerror=1>` appears as `&lt;img`); `test_no_script_tag_and_no_external_urls` (no `<script`, and no `http://` or `https://` in any `src`, `href` or `url(` value); `test_empty_store_renders_a_message`; `test_clicks_panel_totals_7d_and_30d_per_link`; `test_token_panel_shows_days_left_and_needs_attention`; `test_cli_writes_noindex_page`.
- [ ] **Step 2: Run** `pytest tests/dashboard -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with Jinja2 (`autoescape=True`, `StrictUndefined`) and Plan 1's design tokens where they apply (dark mode through `prefers-color-scheme`). `docs/dashboard.md`: private by design, serve it on the LAN or behind the zero-trust access layer, never deploy it to the Pages project; it shows no lead data.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the built-in static dashboard`.

### Task 18: Containers, docs and release v0.2.0

**Files:**
- Modify: `compose.yaml`, `deploy/podman/reach-around.container`, `tests/test_container_smoke.sh`, `examples/instance/site.yaml` (synthetic accounts and alert settings), `docs/containers.md`, `README.md` status table, `CHANGELOG.md`, `pyproject.toml` and `src/reach_around/__init__.py` (version `0.2.0`)
- Create: `docs/numbers.md` (overview, platform table, run locations, `--run-live`), `docs/education/append-only-history.md`

- [ ] **Step 1: Extend the smoke test**: with `examples/instance` at `/config` and a writable `/data`, run `reach-around import /config/numbers.csv` then `reach-around dashboard --out /out`; assert exit `0`, `/out/index.html` exists and contains the imported value and the word `imported`, and `id -u` is not `0`.
- [ ] **Step 2: Run** `CONTAINER_ENGINE=docker bash tests/test_container_smoke.sh`. Expected: FAIL.
- [ ] **Step 3: Implement**: add `/data` (rw volume) to the image and compose with `read_only: true` for the root; add a `collector` service running `schedule` and a Quadlet unit for Podman; extend the example config and add `examples/instance/numbers.csv`; write the docs; bump the version; update the changelog.
- [ ] **Step 4: Run** the smoke test under `docker` and `podman`, `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy`. Expected: all pass.
- [ ] **Step 5: Verify and tag**: CI green on `main`, tag `v0.2.0` (the maintainer has authorized tagging once green), confirm the published image runs the smoke test.
- [ ] **Step 6: Commit** `chore: release v0.2.0`.

---

## Phase 2b: Listeners

Two long-running processes in their own container so a crash never takes the daily collectors down. They write to the same store through the same `Store` interface.

### Task 19: Twitch viewer sampler

**Files:**
- Create: `src/reach_around/listeners/{__init__,twitch_sampler}.py`, `src/reach_around/store/migrations/0003_viewer_samples.sql`, `src/reach_around/commands/listen_twitch.py`, `tests/listeners/test_twitch_sampler.py`, `docs/collectors/twitch-sampler.md`

**Interfaces:**
- Consumes: `app_token` (Task 13), `CollectorContext`, `Store`, `Notifier`, `Alert`.
- Produces: migration tables `viewer_samples(stream_id, account, ts, viewers, PRIMARY KEY (stream_id, ts))` and `stream_final(stream_id PRIMARY KEY, account, day, finalized_at)`; `ViewerSampler(account: Account, ctx: CollectorContext, store: Store, notifiers: Sequence[Notifier], *, live_interval: int = 60, offline_interval: int = 120, offline_grace: int = 300)` with `tick(now: datetime) -> float` (seconds until the next tick) and `rollup(today: date) -> int`; `run_forever(sampler, stop: threading.Event, *, clock: Callable[[], datetime]) -> None`; CLI `reach-around listen-twitch --config PATH [--check]` (`--check` validates config and secret presence, then exits `0`). Polls `GET /helix/streams?user_login=` with an app token; metrics `viewers_peak` and `viewers_avg` on the day the stream started (in `ctx.tz`), written only by `rollup` once the day is over and every stream that started that day is finalized.

- [ ] **Step 1: Write failing tests** (scripted `MockTransport`, fake clock): `test_samples_recorded_while_live`; `test_single_offline_blip_does_not_finalize`; `test_stream_finalized_after_offline_grace` (viewers 10, 30, 20, then offline past 300 s: a `stream_final` row exists); `test_rollup_peak_and_average` (peak 30.0, average 20.0 on the start day, only after that day is over); `test_two_streams_same_day_roll_up_together` (one pair of observations for the day, peak is the max, average is over all samples); `test_stream_crossing_midnight_uses_start_day`; `test_restart_mid_stream_resumes` (a new `ViewerSampler` on the same store keeps earlier samples); `test_orphaned_stream_finalized_on_first_offline_tick` (the process was down when the stream ended); `test_api_error_records_stale_and_writes_no_zero_sample` (500: no sample row, failed run, the next interval doubles up to 300 s); `test_401_refetches_the_app_token_once`; `test_offline_uses_the_slower_interval`; `test_stop_event_ends_the_loop_promptly`; `test_check_flag_exits_zero_without_network`.
- [ ] **Step 2: Run** `pytest tests/listeners/test_twitch_sampler.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** The loop catches every exception from `tick`, records a failed run and backs off; SIGTERM sets `stop`. Docs: the sampler needs only the app token, cannot backfill, and a gap in sampling lowers the average's accuracy, not the peak's.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add the Twitch viewer sampler`.

### Task 20: StreamElements tips listener

**Files:**
- Create: `src/reach_around/listeners/streamelements.py`, `src/reach_around/store/migrations/0004_tip_seen.sql`, `src/reach_around/commands/listen_streamelements.py`, `tests/listeners/test_streamelements.py`, `tests/fixtures/streamelements/*.json`, `docs/collectors/streamelements.md`

**Interfaces:**
- Consumes: `Store`, `Notifier`, `Account`, `SecretBackend`.
- Produces: migration table `tip_seen(transaction_id TEXT PRIMARY KEY, day TEXT, currency TEXT, amount REAL)`; `@dataclass(frozen=True) Tip(transaction_id: str, amount: float, currency: str, created_at: datetime)` (no donor or message field); `parse_tip(message: Mapping[str, object]) -> Tip | None` (None for anything malformed, non-finite or negative); `TipsListener(account, secrets, store, notifiers, *, url: str = "wss://astro.streamelements.com/", connect=websockets.connect, sleep=asyncio.sleep, tz)` with `async run(stop: asyncio.Event) -> None`; `rollup_tips(store, today: date, tz: ZoneInfo) -> int` writes, for each day before `today` that has tips and no observation, metrics `tips_<currency lowercase>` (sum) and `tips_count`. Options `token_type: "jwt" | "apikey" | "oauth2" = "jwt"`; secret `token`. CLI `reach-around listen-streamelements --config PATH [--check]`.

- [ ] **Step 1: Capture the envelope.** Read the official Astro page in `github.com/StreamElements/docs` (branch `master`) and save the authenticate, subscribe and `channel.tips` message shapes, with synthetic values, as fixtures. The plan fixes only what `docs/research/streamelements.md` verified (token fields `token` and `token_type`, topic `channel.tips`, ping every 30 s and close after 70 s without pong, 1 subscribe per 100 ms, HTTP 429 with `Retry-After: 2`). If the docs contradict those, stop and report instead of guessing.
- [ ] **Step 2: Write failing tests** against a local fake server built with `websockets.serve`: `test_authenticates_then_subscribes_to_channel_tips`; `test_duplicate_tip_stored_once` (same `transaction_id` delivered twice); `test_donor_and_message_never_persisted_or_logged` (sentinel donor and message absent from every table and `captured_logs`); `test_malformed_message_is_ignored_and_the_connection_survives`; `test_negative_nan_amounts_dropped`; `test_rollup_only_closed_days_and_only_once` (today's tips are not rolled up; yesterday's become `tips_usd` and `tips_count`; a second call returns `0`); `test_reconnect_backoff_is_1_2_4_up_to_60_seconds_with_jitter` (fake sleep records a bounded, increasing sequence); `test_429_honors_retry_after`; `test_auth_rejected_records_stale_alerts_once_per_day_and_retries_hourly` (not a hot loop); `test_token_never_logged_including_the_auth_frame`.
- [ ] **Step 3: Run** `pytest tests/listeners/test_streamelements.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** and run the tests again. Expected: PASS. Docs: aggregates only by default, the JWT may expire roughly every two weeks (unverified) so a 401 alerts the owner to repaste it, REST history is not used.
- [ ] **Step 5: Commit** `feat: add the StreamElements tips listener`.

### Task 21: Listener container and docs

**Files:**
- Modify: `compose.yaml`, `deploy/podman/` (new `reach-around-listeners.container`), `docs/containers.md`, `docs/numbers.md`, `tests/test_container_smoke.sh`

- [ ] **Step 1: Extend the smoke test**: run `reach-around listen-twitch --check` and `reach-around listen-streamelements --check` in the image against the example config with placeholder environment secrets; assert both exit `0`.
- [ ] **Step 2: Run** the smoke test. Expected: FAIL (service and flags not wired).
- [ ] **Step 3: Implement** a `listeners` compose service (restart `unless-stopped`, shared `/data` volume, read-only root, no ports) and the Quadlet unit; document that the listeners run in their own container and that `store.kind: sqlite` shared across two containers needs WAL (already set), with Postgres recommended when CI also writes.
- [ ] **Step 4: Run** the smoke test under `docker` and `podman`. Expected: PASS. No tag: the listeners ship with the next release.
- [ ] **Step 5: Commit** `feat: run the listeners in their own container`.

---

## Dependencies and parallel order

| Task | Depends on |
|---|---|
| 1 Config | Plan 1 |
| 2 Store core | Plan 1 |
| 3 Postgres | 2 |
| 4 Secrets and registry | 2 |
| 5 Notifiers | 4 |
| 6 Collector framework | 1, 2, 4 |
| 7 Scheduler | 5, 6 |
| 8 to 14 Platform collectors | 6 (12, 13 also 4; 14 also 6 only) |
| 15 Import | 2 |
| 16S Spike: read-only D1 token | 1 |
| 16 Events pull | 1, 2, 5 (alert on a cursor ahead of the source), 6, 16S |
| 16F Signed aggregation route (conditional) | 16, 16S, Plan 1 Task 8 |
| 17 Dashboard | 2, 4 |
| 18 Release | 1 to 17 (16F only if built) |
| 19 Twitch sampler | 13, 5, 6 |
| 20 StreamElements listener | 5, 6 |
| 21 Listener container | 18, 19, 20 |

Waves (tasks inside a wave run in parallel; a wave starts when its dependencies have merged):

1. **Wave A:** 1, 2.
2. **Wave B:** 3, 4, 15, 16S (3, 4 and 15 need only Task 2, and Task 4 also the dependencies Task 1 added; 16S needs Task 1 and a throwaway Cloudflare database).
3. **Wave C:** 5, 6, 17 (17 needs 4 and 2).
4. **Wave D:** 7, 8, 9, 10, 11, 12, 13, 14, 16.
5. **Wave E:** 16F (only if 16S requires it, and only after the maintainer approves the spec amendment), then 18.
6. **Wave F (phase 2b):** 19, 20.
7. **Wave G:** 21.

Tasks 1 and 2 both touch `pyproject.toml` only through Task 1; the store task adds no dependency. No two tasks in a wave edit the same source file: collectors and commands are auto-discovered, and each migration number is owned by one task (0001 Task 2, 0002 Task 4, 0003 Task 19, 0004 Task 20).

## Roadmap coverage

| Roadmap work package | Task |
|---|---|
| Snapshot store (SQLite, optional Postgres) | 2, 3 |
| Token registry, refresh and expiry alerts | 4, 5 |
| Collector framework and scheduler | 6, 7 |
| Bluesky, Mastodon, GitHub, Discord, Matrix, Twitch, Kick | 8, 9, 10, 11, 12, 13, 14 |
| Manual and CSV/XLSX import | 15 |
| Built-in dashboard | 17 |
| Daily pull of Worker events | 16S, 16, 16F (conditional) |
| Twitch viewer sampler, StreamElements tips listener (2b) | 19, 20 |
| Configuration, release, containers (supporting) | 1, 18, 21 |

## Self-review notes

- Spec coverage: collectors behind a common interface, a scheduler, an append-only store with source flags, SQLite default and optional Postgres (4.7); typed and imported values flagged (4.8); token registry, expiry alerts, stale never zero (4.9); built-in dashboard with no Grafana needed (4.10); daily pull into the local store as aggregates, leads kept out, collector-held read token, Worker 90-day window (4.11); the Kick stream key dropped at the edge of its collector (section 6); Docker and Podman with listeners in their own container (section 8). Grafana sinks, exports, summaries and the display-policy table are phases 4 and 5.
- Threat-model additions: per-metric origin tags and owner-authorized retention with a purge (Tasks 2, 6, 7); secret-absence and owner-only permission tests (Tasks 2, 4); aggregate-only pull with a spike on the read-only token and a signed-endpoint fallback that amends spec 4.4 (Tasks 16S, 16, 16F).
- Decisions the spec left open: the observation key includes `source`, reads prefer typed over imported over fetched, and the first value written for a day wins; days are written only when complete; collectors scheduled in CI need a Postgres store; Kick uses an app token only until the live test says otherwise; GitHub days the API omits stay gaps; alert sinks are stdout, a Discord webhook and a Matrix room (email waits for the Mailgun work in phase 3).
