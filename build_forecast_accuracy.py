#!/usr/bin/env python3
"""
Precompute 24-hour-ahead hourly forecast vs actual outdoor temperature.

For each actual hourly outdoor temperature (from meter.csv), this script:
  1. Targets a snapshot pulled ~24 hours before that actual hour.
  2. Uses only snapshots made BEFORE actual_time (never a future forecast).
  3. Requires the snapshot to be within 6 hours of the 24h target time.
  4. Reads the forecasted temperature that snapshot assigned to that same hour.
  5. Writes actual, forecast, and error to forecast_vs_actual_24h.csv.

The dashboard should read only the output CSV (no heavy joins at request time).
Run after meter logging and forecast_logger.py updates.
"""

import bisect
import csv
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
FORECAST_DB = os.path.join(BASE, "forecast_history.db")
OUTPUT_CSV = os.path.join(BASE, "forecast_vs_actual_24h.csv")

OUTPUT_FIELDS = [
    "actual_time",
    "actual_temp",
    "forecast_made_time",
    "hours_before_actual",
    "forecast_for_time",
    "forecast_temp_24h",
    "error",
]

LOOKBACK_HOURS = 24
MAX_SNAPSHOT_DELTA_HOURS = 6  # max |snapshot_time - (actual_time - 24h)|


def load_meter_path():
    with open(CONFIG) as f:
        cfg = json.load(f)
    return os.path.join(BASE, cfg["files"]["csv"])


def parse_local_dt(s):
    """Parse ISO timestamp to naive local wall-clock datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def hour_label(dt):
    """Normalize to hourly key matching hourly_forecast.forecast_time."""
    h = dt.replace(minute=0, second=0, microsecond=0)
    return h.strftime("%Y-%m-%dT%H:%M")


def parse_outdoor_temp(raw):
    """Return finite outdoor °F or None (meter.csv may contain 'nan')."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def load_actual_hourly(meter_path):
    """
    Aggregate meter.csv outdoor_f readings into one average per local hour.
    Returns dict: 'YYYY-MM-DDTHH:MM' -> mean outdoor °F
    """
    buckets = defaultdict(list)
    if not os.path.exists(meter_path):
        return {}

    with open(meter_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp_iso")
            if not ts:
                continue
            temp = parse_outdoor_temp(row.get("outdoor_f"))
            if temp is None:
                continue
            try:
                key = hour_label(parse_local_dt(ts))
            except ValueError:
                continue
            buckets[key].append(temp)

    return {k: sum(v) / len(v) for k, v in buckets.items() if v}


def load_forecast_snapshots(con):
    """
    Load snapshot pull times sorted ascending.
    Returns (pull_times, snapshot_ids, id_to_pulled_at_str).
    """
    cur = con.cursor()
    cur.execute(
        "SELECT id, pulled_at FROM forecast_snapshots ORDER BY pulled_at"
    )
    rows = cur.fetchall()
    pull_times = []
    snapshot_ids = []
    id_to_pulled = {}
    for snap_id, pulled_at in rows:
        pull_times.append(parse_local_dt(pulled_at))
        snapshot_ids.append(snap_id)
        id_to_pulled[snap_id] = pulled_at
    return pull_times, snapshot_ids, id_to_pulled


def load_hourly_forecasts(con):
    """
    Load hourly forecast temps keyed by snapshot_id then forecast_time.
    Returns dict: snapshot_id -> {forecast_time: temp_f}
    """
    cur = con.cursor()
    cur.execute(
        "SELECT snapshot_id, forecast_time, temp_f FROM hourly_forecast"
    )
    by_snapshot = defaultdict(dict)
    for snap_id, forecast_time, temp_f in cur.fetchall():
        if temp_f is not None:
            by_snapshot[snap_id][forecast_time] = temp_f
    return by_snapshot


def find_valid_snapshot_id(actual_dt, pull_times, snapshot_ids):
    """
    Pick the snapshot whose pull time is nearest to (actual_dt - 24 hours),
    but only among snapshots that:
      - were made strictly before actual_dt (no future forecasts)
      - are within MAX_SNAPSHOT_DELTA_HOURS of the 24h target
    """
    if not pull_times:
        return None

    target = actual_dt - timedelta(hours=LOOKBACK_HOURS)
    max_delta_sec = MAX_SNAPSHOT_DELTA_HOURS * 3600

    # Indices 0..end-1 are snapshots with pull_time < actual_dt.
    end = bisect.bisect_left(pull_times, actual_dt)
    if end == 0:
        return None

    valid_times = pull_times[:end]
    valid_ids = snapshot_ids[:end]

    idx = bisect.bisect_left(valid_times, target)
    best_idx = None
    best_delta = None
    for candidate in (idx - 1, idx):
        if 0 <= candidate < len(valid_times):
            delta = abs((valid_times[candidate] - target).total_seconds())
            if delta > max_delta_sec:
                continue
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_idx = candidate

    return valid_ids[best_idx] if best_idx is not None else None


def build_rows(actual_hourly, pull_times, snapshot_ids, id_to_pulled, forecasts):
    """
    Match each actual hour to a ~24h-old snapshot forecast for that same hour.
    """
    rows = []
    for actual_time in sorted(actual_hourly):
        actual_dt = parse_local_dt(actual_time)
        actual_temp = actual_hourly[actual_time]

        snap_id = find_valid_snapshot_id(actual_dt, pull_times, snapshot_ids)
        if snap_id is None:
            continue

        forecast_temp = forecasts.get(snap_id, {}).get(actual_time)
        if forecast_temp is None:
            # Snapshot may not have covered this hour (48h window at pull time).
            continue

        made_dt = parse_local_dt(id_to_pulled[snap_id])
        hours_before = (actual_dt - made_dt).total_seconds() / 3600

        rows.append({
            "actual_time": actual_time,
            "actual_temp": round(actual_temp, 2),
            "forecast_made_time": id_to_pulled[snap_id],
            "hours_before_actual": round(hours_before, 2),
            "forecast_for_time": actual_time,
            "forecast_temp_24h": round(forecast_temp, 2),
            "error": round(actual_temp - forecast_temp, 2),
        })

    return rows


def write_output(rows):
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    meter_path = load_meter_path()
    actual_hourly = load_actual_hourly(meter_path)
    if not actual_hourly:
        print(f"No hourly actual temps found in {meter_path}")
        write_output([])
        return

    if not os.path.exists(FORECAST_DB):
        print(f"No forecast DB: {FORECAST_DB}")
        write_output([])
        return

    con = sqlite3.connect(FORECAST_DB)
    try:
        pull_times, snapshot_ids, id_to_pulled = load_forecast_snapshots(con)
        forecasts = load_hourly_forecasts(con)
    finally:
        con.close()

    if not pull_times:
        print("No forecast snapshots in database")
        write_output([])
        return

    rows = build_rows(
        actual_hourly, pull_times, snapshot_ids, id_to_pulled, forecasts
    )
    write_output(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
