#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Estimate a monthly Cloudflare bill for a Reach Around deployment.

Usage: python3 -I scripts/cost_estimate.py [--example small|medium|busy]
       [--views N] [--sites N] [--clicks N] [--leads N]
       [--raw-keep-days N] [--lead-retention-days N]
       [--views-per-hour-per-site N] [--account-views-per-hour N] [--json]

Offline and read-only: it does arithmetic on the numbers in PRICES and prints a
table. It opens no network connection, reads no file and needs no credential.

This is an estimate, not a quote. The prices are Cloudflare's published prices
on the date in PRICES_READ and can change. The per-operation write counts come
from the repo's own budget model (worker/src/pvlimits.ts and
worker/src/kit/budgets.ts). The numbers marked INFERRED below are assumptions,
not measurements. See docs/wiki/Costs.md for the full method.

Only what the repo uses today is priced: Workers, D1, static hosting and
Turnstile. Durable Objects, KV, R2 and Analytics Engine are planned or
optional and are not in this estimate.

Exit: 0 on success, 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PRICES_READ = "2026-11-24"

# Published prices (Workers and D1 pages, read on PRICES_READ).
# https://developers.cloudflare.com/workers/platform/pricing/
# https://developers.cloudflare.com/d1/platform/pricing/
PRICES = {
    "workers_paid_minimum": Decimal("5"),
    "workers_requests_included": Decimal(10_000_000),
    "workers_requests_per_million": Decimal("0.30"),
    "workers_cpu_ms_included": Decimal(30_000_000),
    "workers_cpu_per_million_ms": Decimal("0.02"),
    "d1_rows_read_included": Decimal(25_000_000_000),
    "d1_rows_read_per_million": Decimal("0.001"),
    "d1_rows_written_included": Decimal(50_000_000),
    "d1_rows_written_per_million": Decimal("1.00"),
    "d1_storage_gb_included": Decimal(5),
    "d1_storage_per_gb_month": Decimal("0.75"),
}

# Free plan daily limits (same pages).
FREE_WORKERS_REQUESTS_PER_DAY = 100_000
FREE_D1_ROWS_WRITTEN_PER_DAY = 100_000
FREE_D1_ROWS_READ_PER_DAY = 5_000_000
# The repo's documented Free pair for the click ceiling (docs/deploy.md).
FREE_CLICK_WRITES_PER_DAY = 7_500

# From the repo's budget model.
DAYS = 30
WRITES_PER_VIEW = Decimal(28) / Decimal(5)  # 5.6, worker/src/pvlimits.ts
WRITES_PER_CLICK = 9  # the charge for a site with no optional lists (docs/deploy.md)
WRITES_PER_LEAD = 6  # gate 5 plus claim 1, worker/src/kit/budgets.ts
REQUESTS_PER_LEAD = 2  # the gate request and the gated page view (INFERRED)
FIXED_WRITES_PER_SITE_PER_DAY = 2_540  # rollup, purge, heartbeat (pvlimits.ts)
CRON_RUNS_PER_DAY = 24
BYTES_PER_LEAD = 600  # worker/src/kit/budgets.ts
AGGREGATE_ROWS_PER_SITE_PER_DAY = 1_125  # worst case, docs/deploy.md
AGGREGATE_KEEP_DAYS = 90
DEFAULT_VIEWS_PER_HOUR_PER_SITE = 120
DEFAULT_ACCOUNT_VIEWS_PER_HOUR = 480

# INFERRED assumptions (not measured; replace with your own numbers).
CPU_MS_PER_REQUEST = 3
CPU_MS_PER_CRON_RUN = 500
ROWS_READ_PER_REQUEST = 10
BYTES_PER_RAW_ROW = 200
BYTES_PER_AGGREGATE_ROW = 100
GB = Decimal(1_000_000_000)

EXAMPLES: dict[str, dict[str, int]] = {
    "small": {
        "views": 20_000,
        "sites": 1,
        "clicks": 3_000,
        "leads": 50,
        "raw_keep_days": 7,
        "lead_retention_days": 30,
        "views_per_hour_per_site": 120,
        "account_views_per_hour": 480,
    },
    "medium": {
        "views": 300_000,
        "sites": 3,
        "clicks": 45_000,
        "leads": 600,
        "raw_keep_days": 7,
        "lead_retention_days": 30,
        "views_per_hour_per_site": 400,
        "account_views_per_hour": 1_200,
    },
    "busy": {
        "views": 10_000_000,
        "sites": 5,
        "clicks": 1_500_000,
        "leads": 20_000,
        "raw_keep_days": 7,
        "lead_retention_days": 30,
        "views_per_hour_per_site": 4_000,
        "account_views_per_hour": 20_000,
    },
}


@dataclass(frozen=True)
class Inputs:
    views: int
    sites: int
    clicks: int
    leads: int
    raw_keep_days: int = 7
    lead_retention_days: int = 30
    views_per_hour_per_site: int = DEFAULT_VIEWS_PER_HOUR_PER_SITE
    account_views_per_hour: int = DEFAULT_ACCOUNT_VIEWS_PER_HOUR


def money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def over(used: Decimal, included: Decimal) -> Decimal:
    return max(Decimal(0), used - included)


def estimate(i: Inputs) -> dict[str, object]:
    """Return every line of the estimate. All money is US dollars a month."""
    p = PRICES
    views, clicks, leads, sites = (
        Decimal(i.views),
        Decimal(i.clicks),
        Decimal(i.leads),
        i.sites,
    )

    cron_runs = Decimal(CRON_RUNS_PER_DAY * DAYS)
    requests = views + clicks + leads * REQUESTS_PER_LEAD + cron_runs
    cpu_ms = requests * CPU_MS_PER_REQUEST + cron_runs * CPU_MS_PER_CRON_RUN
    req_over = over(requests, p["workers_requests_included"])
    cpu_over = over(cpu_ms, p["workers_cpu_ms_included"])
    req_cost = req_over / 1_000_000 * p["workers_requests_per_million"]
    cpu_cost = cpu_over / 1_000_000 * p["workers_cpu_per_million_ms"]

    view_writes = views * WRITES_PER_VIEW
    click_writes = clicks * WRITES_PER_CLICK
    lead_writes = leads * WRITES_PER_LEAD
    fixed_writes = Decimal(FIXED_WRITES_PER_SITE_PER_DAY * DAYS * sites)
    writes = view_writes + click_writes + lead_writes + fixed_writes
    writes_over = over(writes, p["d1_rows_written_included"])
    writes_cost = writes_over / 1_000_000 * p["d1_rows_written_per_million"]

    rows_read = requests * ROWS_READ_PER_REQUEST
    reads_over = over(rows_read, p["d1_rows_read_included"])
    reads_cost = reads_over / 1_000_000 * p["d1_rows_read_per_million"]

    raw_bytes = (views + clicks) / DAYS * i.raw_keep_days * BYTES_PER_RAW_ROW
    lead_bytes = leads / DAYS * i.lead_retention_days * BYTES_PER_LEAD
    agg_bytes = Decimal(
        AGGREGATE_ROWS_PER_SITE_PER_DAY
        * sites
        * AGGREGATE_KEEP_DAYS
        * BYTES_PER_AGGREGATE_ROW
    )
    storage_gb = (raw_bytes + lead_bytes + agg_bytes) / GB
    storage_over = over(storage_gb, p["d1_storage_gb_included"])
    storage_cost = storage_over * p["d1_storage_per_gb_month"]

    floor = p["workers_paid_minimum"]
    total = floor + req_cost + cpu_cost + writes_cost + reads_cost + storage_cost

    daily_writes = writes / DAYS
    daily_click_writes = click_writes / DAYS
    daily_requests = requests / DAYS
    free_fits = (
        daily_writes <= FREE_D1_ROWS_WRITTEN_PER_DAY
        and daily_click_writes <= FREE_CLICK_WRITES_PER_DAY
        and daily_requests <= FREE_WORKERS_REQUESTS_PER_DAY
        and rows_read / DAYS <= FREE_D1_ROWS_READ_PER_DAY
    )
    binding = min(sites * i.views_per_hour_per_site, i.account_views_per_hour)
    avg_hour = views / (DAYS * 24)
    return {
        "requests": int(requests),
        "worker_cpu_ms": int(cpu_ms),
        "d1_rows_written": int(writes),
        "d1_rows_read": int(rows_read),
        "d1_storage_gb": str(storage_gb.quantize(Decimal("0.001"))),
        "daily_rows_written": int(daily_writes),
        "daily_click_rows_written": int(daily_click_writes),
        "avg_views_per_hour": str(avg_hour.quantize(Decimal("0.1"))),
        "binding_human_views_per_hour": binding,
        "view_budget_ok": avg_hour <= binding,
        "free_plan_fits": free_fits,
        "plan": "Free possible (see notes)" if free_fits else "Paid",
        "cost_workers_floor": str(money(floor)),
        "cost_workers_requests": str(money(req_cost)),
        "cost_workers_cpu": str(money(cpu_cost)),
        "cost_d1_rows_written": str(money(writes_cost)),
        "cost_d1_rows_read": str(money(reads_cost)),
        "cost_d1_storage": str(money(storage_cost)),
        "cost_total": str(money(total)),
    }


def render(i: Inputs, r: dict[str, object]) -> str:
    rows = [
        ("Workers requests a month", f"{r['requests']:,}"),
        ("Workers CPU ms a month (INFERRED 3 ms a request)", f"{r['worker_cpu_ms']:,}"),
        ("D1 rows written a month", f"{r['d1_rows_written']:,}"),
        ("D1 rows written a day", f"{r['daily_rows_written']:,}"),
        ("D1 rows read a month (INFERRED)", f"{r['d1_rows_read']:,}"),
        ("D1 storage GB (INFERRED)", f"{r['d1_storage_gb']}"),
        ("Average views an hour", f"{r['avg_views_per_hour']}"),
        (
            "Human views an hour the budget allows",
            f"{r['binding_human_views_per_hour']:,}",
        ),
        (
            "Hourly view budget covers the average",
            "yes" if r["view_budget_ok"] else "NO: raise it",
        ),
        ("Plan", f"{r['plan']}"),
        ("Cost: Workers Paid floor", f"${r['cost_workers_floor']}"),
        ("Cost: Workers requests over included", f"${r['cost_workers_requests']}"),
        ("Cost: Workers CPU over included", f"${r['cost_workers_cpu']}"),
        ("Cost: D1 rows written over included", f"${r['cost_d1_rows_written']}"),
        ("Cost: D1 rows read over included", f"${r['cost_d1_rows_read']}"),
        ("Cost: D1 storage over included", f"${r['cost_d1_storage']}"),
        ("TOTAL a month", f"${r['cost_total']}"),
    ]
    width = max(len(k) for k, _ in rows)
    out = [f"Prices as of {PRICES_READ}. An estimate, not a quote.", ""]
    out += [f"{k.ljust(width)}  {v}" for k, v in rows]
    return "\n".join(out)


def parse(argv: list[str]) -> tuple[Inputs, bool] | None:
    ap = argparse.ArgumentParser(description="Estimate a monthly Cloudflare bill.")
    ap.add_argument("--example", choices=sorted(EXAMPLES))
    ap.add_argument("--views", type=int)
    ap.add_argument("--sites", type=int)
    ap.add_argument("--clicks", type=int)
    ap.add_argument("--leads", type=int)
    ap.add_argument("--raw-keep-days", type=int)
    ap.add_argument("--lead-retention-days", type=int)
    ap.add_argument("--views-per-hour-per-site", type=int)
    ap.add_argument("--account-views-per-hour", type=int)
    ap.add_argument("--json", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return None
    base = dict(EXAMPLES[a.example]) if a.example else {}
    for key in (
        "views",
        "sites",
        "clicks",
        "leads",
        "raw_keep_days",
        "lead_retention_days",
        "views_per_hour_per_site",
        "account_views_per_hour",
    ):
        v = getattr(a, key)
        if v is not None:
            base[key] = v
    needed = ("views", "sites", "clicks", "leads")
    if any(k not in base for k in needed):
        print(
            "error: give --example or all of --views --sites --clicks --leads",
            file=sys.stderr,
        )
        return None
    if any(v < 0 for v in base.values()) or base["sites"] < 1:
        print(
            "error: numbers must be whole and not negative; sites at least 1",
            file=sys.stderr,
        )
        return None
    return Inputs(**base), a.json


def main(argv: list[str] | None = None) -> int:
    parsed = parse(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return 2
    inputs, as_json = parsed
    result = estimate(inputs)
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(inputs, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
