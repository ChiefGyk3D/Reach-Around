# SPDX-License-Identifier: Apache-2.0
"""Load an owner-supplied D1 export for ``reach-around report``.

The export is semi-trusted input: a file the owner made from the Worker's
aggregate tables, possibly by hand. This loader fails closed. It reads the file
through the repo's bounded no-follow reader, caps size and row counts, refuses
duplicate JSON keys, ``NaN``, ``Infinity`` and deep nesting, and accepts only
the columns listed in ``SPECS``. Every other column is refused, and a column
that looks like an address, a user agent, a lead or a header is refused with a
message that says so. Text values are checked too: a value that looks like an
IP address or a browser user-agent string, or that holds a control character,
refuses the whole file. An error message never repeats a value from the file.

Accepted shapes, per file:

* JSON ``{"version": 1, "tables": {"<table>": [rows...]}}``
* JSON list of rows, or of ``wrangler d1 execute --json`` blocks
  (``[{"results": [rows...], ...}]``); the table is recognized by its columns
* CSV with a header row, one table per file, recognized by its columns

No network, no clock and no Cloudflare call is involved.
"""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from reach_around.config.loader import ConfigError, _read

MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 200_000
MAX_LABEL = 96
MAX_INT = 10**12
MAX_TS = 4_102_444_800_000  # 2100-01-01 UTC in milliseconds

CLASSES = ("h", "v", "a", "s")
SRC_KINDS = ("d", "i", "r", "u", "o")
DIMS = ("browser", "os", "device", "lang", "vp", "net", "reason")
KIT_KINDS = ("unlock", "view", "verify")


class ExportError(ValueError):
    """The export is unusable. The message never contains a value from it."""


@dataclass(frozen=True)
class Spec:
    marker: str  # a column only this table has; used to recognize the table
    columns: dict[str, str]  # column -> kind
    required: frozenset[str]
    key: tuple[str, ...] = ()  # primary key columns; empty = rows may repeat


SPECS: dict[str, Spec] = {
    "view_daily": Spec(
        marker="src_kind",
        columns={
            "site": "site",
            "day": "day",
            "day_tz": "tz",
            "page": "path",
            "src_kind": "src_kind",
            "src": "src",
            "country": "country",
            "bot_class": "class",
            "views": "pos",
            "uniques": "uint",
        },
        required=frozenset(
            {"day", "page", "src_kind", "src", "country", "bot_class", "views"}
        ),
        key=("site", "day", "page", "src_kind", "src", "country", "bot_class"),
    ),
    "view_dim_daily": Spec(
        marker="dim",
        columns={
            "site": "site",
            "day": "day",
            "day_tz": "tz",
            "dim": "dim",
            "value": "dimvalue",
            "bot_class": "class",
            "views": "pos",
            "uniques": "uint",
        },
        required=frozenset({"day", "dim", "value", "bot_class", "views"}),
        key=("site", "day", "dim", "value", "bot_class"),
    ),
    "view_geo_daily": Spec(
        marker="city",
        columns={
            "site": "site",
            "day": "day",
            "day_tz": "tz",
            "country": "country",
            "region": "region",
            "city": "city",
            "bot_class": "class",
            "views": "pos",
            "uniques": "uint",
        },
        required=frozenset({"day", "country", "region", "city", "bot_class", "views"}),
        key=("site", "day", "country", "region", "city", "bot_class"),
    ),
    # Raw rows: select only these columns. There is no key; rows may repeat.
    "click_events": Spec(
        marker="link_id",
        columns={"site": "site", "ts": "ts", "link_id": "slug", "country": "country0"},
        required=frozenset({"ts", "link_id"}),
    ),
    "kit_events": Spec(
        marker="kind",
        columns={"site": "site", "ts": "ts", "kind": "kit_kind"},
        required=frozenset({"ts", "kind"}),
    ),
}
_MARKERS = {spec.marker: name for name, spec in SPECS.items()}

_SITE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_COUNTRY = re.compile(r"[A-Z]{2}")
_CSV_INT = re.compile(r"\d{1,13}")
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
_IPV6 = re.compile(r"[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,}")
_UA = re.compile(
    r"mozilla/|applewebkit|gecko/|(?:chrome|safari|firefox|edg|opr)/\d|"
    r"curl/|wget/|python-requests|python-urllib|okhttp|googlebot/",
    re.IGNORECASE,
)
_FORBIDDEN_NAME = re.compile(
    r"(^|_)(ip|ips|ipv4|ipv6|addr|address|ua|agent|header|headers|cookie|email|"
    r"lead|leads|asn|referer|referrer_url|url)(_|$)|useragent|user_agent",
    re.IGNORECASE,
)


def _safe_name(name: str) -> str:
    """A fixed-length stand-in for an untrusted name; never the name itself."""
    digest = hashlib.sha256(name.encode("utf-8", "replace")).hexdigest()[:8]
    return f"<name {digest}>"


@dataclass
class Export:
    """Validated rows. A table missing from the export is absent from ``present``."""

    view_daily: list[dict[str, Any]] = field(default_factory=list)
    view_dim_daily: list[dict[str, Any]] = field(default_factory=list)
    view_geo_daily: list[dict[str, Any]] = field(default_factory=list)
    click_events: list[dict[str, Any]] = field(default_factory=list)
    kit_events: list[dict[str, Any]] = field(default_factory=list)
    present: set[str] = field(default_factory=set)


_PATH = re.compile(r"[a-z0-9/_.-]{1,96}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,78}[a-z0-9])?")
_UTM = re.compile(r"[a-z0-9-]{1,30}")
_REGION = re.compile(r"[A-Z0-9]{0,3}")
_CITY = re.compile(r"(?:[^\W\d_]|[ .'\u2019-]){0,60}")
_DIMVALUE = re.compile(r"[a-z0-9_.-]{1,30}")
_SRC = re.compile(r"[a-z0-9.-]{0,80}")
_LONG_RUN = re.compile(r"[A-Za-z0-9]{24,}")
BOT_REASONS = (
    "ua-declared-bot",
    "ua-headless",
    "ua-missing",
    "ua-too-long",
    "no-lang",
    "no-sec-fetch",
    "datacenter",
    "tor",
    "no-js",
    "click-without-view",
    "rate",
    "paid-score",
    "verified-crawler-ua",
    "none",
)
_MAX_DECODE_ROUNDS = 3
_TEXT_KINDS = {
    "path": _PATH,
    "slug": _SLUG,
    "region": _REGION,
    "city": _CITY,
    "dimvalue": _DIMVALUE,
    "src": _SRC,
}


def _screen_text(value: str, where: str) -> None:
    """Defense in depth behind the strict per-field formats.

    The text is percent-decoded a bounded number of times, NFKC-normalized and
    case-folded before the address, user-agent, email and token patterns are
    applied, so encoded, full-width and compatibility forms are seen as what
    they are.
    """
    text = value
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    else:
        if unquote(text) != text:
            raise ExportError(f"{where}: text is encoded too deeply")
    text = unicodedata.normalize("NFKC", text).casefold()
    if "@" in text:
        raise ExportError(f"{where}: a value looks like an email address")
    if _UA.search(text):
        raise ExportError(f"{where}: a value looks like a user-agent string")
    if _IPV4.search(text) or _IPV6.search(text) or _is_ip(text):
        raise ExportError(f"{where}: a value looks like an IP address")
    if _LONG_RUN.search(text):
        raise ExportError(f"{where}: a value looks like a token")


def _check_text(value: Any, *, pattern: re.Pattern[str], where: str) -> str:
    """Strict format first, then the screen."""
    if not isinstance(value, str):
        raise ExportError(f"{where}: text expected")
    if len(value) > MAX_LABEL:
        raise ExportError(f"{where}: text longer than {MAX_LABEL} characters")
    if not value.isprintable():
        raise ExportError(f"{where}: control or invisible characters in text")
    if unicodedata.normalize("NFKC", value) != value:
        raise ExportError(f"{where}: text is not in normalized form")
    if not pattern.fullmatch(value):
        raise ExportError(f"{where}: value has the wrong form")
    _screen_text(value, where)
    return value


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


def _check_int(value: Any, *, low: int, high: int, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExportError(f"{where}: whole number expected")
    if not low <= value <= high:
        raise ExportError(f"{where}: number out of range")
    return value


def _check_value(kind: str, value: Any, where: str) -> Any:
    if kind in _TEXT_KINDS:
        return _check_text(value, pattern=_TEXT_KINDS[kind], where=where)
    if kind == "pos":
        return _check_int(value, low=1, high=MAX_INT, where=where)
    if kind == "uint":
        return _check_int(value, low=0, high=MAX_INT, where=where)
    if kind == "ts":
        return _check_int(value, low=0, high=MAX_TS, where=where)
    pattern: re.Pattern[str] | None = None
    choices: tuple[str, ...] | None = None
    if kind == "site":
        pattern = _SITE
    elif kind == "day":
        pattern = _DAY
    elif kind == "country":
        pattern = _COUNTRY
    elif kind == "country0":
        if value is None or value == "":
            return ""
        pattern = _COUNTRY
    elif kind == "tz":
        if value != "UTC":
            raise ExportError(f"{where}: only UTC days are supported")
        return "UTC"
    elif kind == "class":
        choices = CLASSES
    elif kind == "src_kind":
        choices = SRC_KINDS
    elif kind == "dim":
        choices = DIMS
    elif kind == "kit_kind":
        choices = KIT_KINDS
    else:  # pragma: no cover - a spec typo
        raise AssertionError(kind)
    if not isinstance(value, str):
        raise ExportError(f"{where}: text expected")
    if choices is not None:
        if value not in choices:
            raise ExportError(f"{where}: value is not one of the allowed codes")
        return value
    if pattern is None or not pattern.fullmatch(value):
        raise ExportError(f"{where}: value has the wrong form")
    if kind == "day":
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ExportError(f"{where}: not a real date") from None
    return value


def screen_columns(names: Any, where: str) -> None:
    """Refuse any column that looks like an address, agent, header or lead."""
    for name in names:
        if not isinstance(name, str):
            raise ExportError(f"{where}: column name is not text")
        if _FORBIDDEN_NAME.search(name):
            raise ExportError(
                f"{where}: forbidden column {_safe_name(name)!r}: the export must "
                "not carry addresses, user agents, headers or lead data"
            )


def _cross_check(table: str, row: dict[str, Any], where: str) -> None:
    """Rules that tie one column to another."""
    if table == "view_daily":
        kind, src = row["src_kind"], row["src"]
        if kind in ("d", "i") and src:
            raise ExportError(f"{where}: a direct or internal view has no source")
        if kind == "o" and src not in ("", "other"):
            raise ExportError(f"{where}: source of kind 'o' must be empty or 'other'")
        if kind == "u" and not _UTM.fullmatch(src):
            raise ExportError(f"{where}: utm source has the wrong form")
        if kind == "r":
            if not _HOST.fullmatch(src) or not re.search(
                r"[a-z]", src.rsplit(".", 1)[-1]
            ):
                raise ExportError(f"{where}: referrer must be a host name")
    elif table == "view_dim_daily":
        if row["dim"] == "reason" and row["value"] not in (*BOT_REASONS, "other"):
            raise ExportError(f"{where}: unknown reason code")


def _validate_row(table: str, row: Any, where: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ExportError(f"{where}: a row must be an object")
    spec = SPECS[table]
    screen_columns(row.keys(), where)
    for name in row:
        if name not in spec.columns:
            raise ExportError(f"{where}: unknown column {_safe_name(name)!r}")
    for name in sorted(spec.required):
        if name not in row:
            raise ExportError(f"{where}: missing column {name!r}")
    out: dict[str, Any] = {}
    for name, kind in spec.columns.items():
        if name in row:
            out[name] = _check_value(kind, row[name], f"{where}, column {name!r}")
    # The kit has no site in the database. A kit row without one stays unscoped.
    _cross_check(table, out, where)
    if "site" in spec.columns and table != "kit_events":
        out.setdefault("site", "links")
    return out


def _infer_table(names: Any, where: str) -> str:
    found = {_MARKERS[name] for name in names if name in _MARKERS}
    if len(found) != 1:
        raise ExportError(f"{where}: cannot tell which table these rows are from")
    return next(iter(found))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ExportError("duplicate key in the JSON")
        out[key] = value
    return out


def _bad_constant(_: str) -> Any:
    raise ExportError("NaN and Infinity are not allowed")


def _rows_from_json(data: Any, where: str) -> dict[str, list[Any]]:
    """Map table name to its raw rows, from either JSON shape."""
    tables: dict[str, list[Any]] = {}
    if isinstance(data, dict):
        extra = set(data) - {"version", "tables"}
        if extra:
            raise ExportError(f"{where}: unexpected top-level key")
        if "version" in data and data["version"] != 1:
            raise ExportError(f"{where}: unsupported export version")
        raw = data.get("tables")
        if not isinstance(raw, dict) or not raw:
            raise ExportError(f"{where}: expected a non-empty 'tables' object")
        for name, rows in raw.items():
            if name not in SPECS:
                raise ExportError(
                    f"{where}: table {_safe_name(str(name))!r} is not allowed"
                )
            if not isinstance(rows, list):
                raise ExportError(f"{where}: table {name!r} must be a list of rows")
            tables[name] = rows
        return tables
    if isinstance(data, list):
        flat: list[Any] = []
        for item in data:
            if isinstance(item, dict) and "results" in item:
                if not set(item) <= {"results", "success", "meta"}:
                    raise ExportError(f"{where}: unexpected key in a result block")
                results = item["results"]
                if not isinstance(results, list):
                    raise ExportError(f"{where}: 'results' must be a list")
                flat.extend(results)
            else:
                flat.append(item)
        if not flat:
            raise ExportError(f"{where}: no rows")
        for row in flat:
            if not isinstance(row, dict):
                raise ExportError(f"{where}: a row must be an object")
            screen_columns(row.keys(), where)
            tables.setdefault(_infer_table(row.keys(), where), []).append(row)
        return tables
    raise ExportError(f"{where}: expected a JSON object or list")


def _rows_from_csv(text: str, where: str) -> dict[str, list[Any]]:
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, None)
        if not header:
            raise ExportError(f"{where}: empty CSV")
        if len(set(header)) != len(header):
            raise ExportError(f"{where}: duplicate column in the CSV header")
        screen_columns(header, where)
        table = _infer_table(header, where)
        spec = SPECS[table]
        for name in header:
            if name not in spec.columns:
                raise ExportError(f"{where}: unknown column {_safe_name(name)!r}")
        rows: list[Any] = []
        for line in reader:
            if len(rows) >= MAX_ROWS:
                raise ExportError(f"{where}: more than {MAX_ROWS} rows")
            if len(line) != len(header):
                raise ExportError(
                    f"{where}: CSV row {len(rows) + 2} has the wrong width"
                )
            row: dict[str, Any] = {}
            for name, cell in zip(header, line, strict=True):
                if spec.columns[name] in ("pos", "uint", "ts"):
                    if cell == "" and name not in spec.required:
                        continue
                    if not _CSV_INT.fullmatch(cell):
                        raise ExportError(
                            f"{where}: CSV row {len(rows) + 2}: not a whole number"
                        )
                    row[name] = int(cell)
                else:
                    row[name] = cell
            rows.append(row)
    except csv.Error:
        raise ExportError(f"{where}: the CSV is malformed") from None
    return {table: rows}


def _read_file(path: Path) -> dict[str, list[Any]]:
    where = path.name
    try:
        raw = _read(path, MAX_EXPORT_BYTES)
    except ConfigError as exc:
        raise ExportError(f"{where}: {exc.message}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExportError(f"{where}: not valid UTF-8") from None
    head = text.lstrip()[:1]
    if head in ("{", "["):
        try:
            data = json.loads(
                text,
                object_pairs_hook=_no_duplicates,
                parse_constant=_bad_constant,
            )
        except ExportError as exc:
            raise ExportError(f"{where}: {exc}") from None
        except RecursionError:
            raise ExportError(f"{where}: JSON nested too deeply") from None
        except ValueError:
            raise ExportError(f"{where}: not valid JSON") from None
        return _rows_from_json(data, where)
    return _rows_from_csv(text, where)


def load_exports(paths: list[Path]) -> Export:
    """Read and validate every input; each table may come from one file only."""
    if not paths:
        raise ExportError("no input file given")
    export = Export()
    for path in paths:
        where = path.name
        for table, rows in _read_file(path).items():
            if table in export.present:
                raise ExportError(f"{where}: table {table!r} was given twice")
            if len(rows) > MAX_ROWS:
                raise ExportError(
                    f"{where}: table {table!r} has more than {MAX_ROWS} rows"
                )
            spec = SPECS[table]
            seen: set[tuple[Any, ...]] = set()
            clean: list[dict[str, Any]] = []
            for index, row in enumerate(rows, start=1):
                item = _validate_row(table, row, f"{where}, {table} row {index}")
                if spec.key:
                    ident = tuple(item.get(name) for name in spec.key)
                    if ident in seen:
                        raise ExportError(
                            f"{where}: {table} row {index} repeats an earlier key"
                        )
                    seen.add(ident)
                clean.append(item)
            getattr(export, table).extend(clean)
            export.present.add(table)
    return export
