#!/usr/bin/env python3
import os, csv, json, time
from datetime import datetime, date

import requests

BASE = os.path.dirname(__file__)
def pjoin(x): return os.path.join(BASE, x)

print("[DEBUG] run_once.py starting up")

# ---------------- Load config for log path + location ----------------
with open(pjoin("config.json")) as f:
    CFG = json.load(f)

CSV_PATH = pjoin(CFG["files"]["csv"])   # main HVAC log csv used by meter.py
LAT = CFG["location"]["lat"]
LON = CFG["location"]["lon"]

WEATHER_CSV = pjoin("Weather_history.csv")


def load_existing_weather_dates():
    """Return set of dates already present in Weather_history.csv (YYYY-MM-DD)."""
    dates = set()
    if os.path.exists(WEATHER_CSV):
        print(f"[DEBUG] Found existing {WEATHER_CSV}")
        with open(WEATHER_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = row.get("date")
                if d:
                    dates.add(d)
    else:
        print(f"[DEBUG] {WEATHER_CSV} does not exist yet")
    return dates


def extract_dates_from_log():
    """Scan the main HVAC CSV and return a set of unique dates (YYYY-MM-DD)."""
    dates = set()
    if not os.path.exists(CSV_PATH):
        print(f"[ERROR] Log CSV not found: {CSV_PATH}")
        return dates

    print(f"[DEBUG] Reading log CSV: {CSV_PATH}")
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("[ERROR] Log CSV has no header/fieldnames")
            return dates

        print(f"[DEBUG] Log CSV columns: {reader.fieldnames}")
        fieldnames_lower = [fn.lower() for fn in reader.fieldnames]

        if "date" in fieldnames_lower:
            date_col = reader.fieldnames[fieldnames_lower.index("date")]
        elif "timestamp" in fieldnames_lower:
            date_col = reader.fieldnames[fieldnames_lower.index("timestamp")]
        elif "time" in fieldnames_lower:
            date_col = reader.fieldnames[fieldnames_lower.index("time")]
        else:
            date_col = reader.fieldnames[0]
            print(f"[WARN] Using first column as date source: {date_col}")

        for i, row in enumerate(reader):
            raw = row.get(date_col, "")
            if not raw:
                continue

            ds = raw[:10]  # expect 'YYYY-MM-DD...' in front
            try:
                _ = datetime.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue
            dates.add(ds)

            # Show a few samples for debugging
            if i < 5:
                print(f"[DEBUG] Sample row {i}: raw={raw!r}, parsed date={ds}")

    # Only keep days in the past
    today = date.today()
    past_dates = set()
    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        if d < today:
            past_dates.add(ds)

    print(f"[DEBUG] Found {len(past_dates)} unique past date(s) in log")
    return past_dates


def fetch_daily_weather(ds):
    """Fetch max/min/mean temp (°F) for a given YYYY-MM-DD date string."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
        f"&start_date={ds}&end_date={ds}"
        "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"
        "&temperature_unit=fahrenheit"
    )
    print(f"[DEBUG] Requesting weather for {ds}: {url}")
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    j = r.json()
    dly = j["daily"]
    return {
        "date": ds,
        "avg_f": dly["temperature_2m_mean"][0],
        "high_f": dly["temperature_2m_max"][0],
        "low_f": dly["temperature_2m_min"][0],
    }


def main():
    print(f"[INFO] Log CSV: {CSV_PATH}")
    print(f"[INFO] Weather CSV: {WEATHER_CSV}")

    log_dates = extract_dates_from_log()
    if not log_dates:
        print("[INFO] No valid dates found in log, nothing to do.")
        return

    existing = load_existing_weather_dates()
    if existing:
        print(f"[INFO] Weather_history.csv already has {len(existing)} date(s).")

    pending = sorted(d for d in log_dates if d not in existing)
    if not pending:
        print("[INFO] All log dates are already in Weather_history.csv.")
        return

    print(f"[INFO] Need to fetch weather for {len(pending)} date(s): {pending}")

    rows_to_append = []
    for ds in pending:
        print(f"[INFO] Fetching {ds} ...")
        try:
            row = fetch_daily_weather(ds)
            rows_to_append.append(row)
            print(f"[INFO] {ds}: avg={row['avg_f']}, high={row['high_f']}, low={row['low_f']}")
        except Exception as e:
            print(f"[ERROR] fetching {ds}: {e}")
        time.sleep(0.3)

    if not rows_to_append:
        print("[INFO] Nothing to write.")
        return

    file_exists = os.path.exists(WEATHER_CSV)
    with open(WEATHER_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "avg_f", "high_f", "low_f"])
        if not file_exists:
            print(f"[INFO] Creating new {WEATHER_CSV} with header")
            writer.writeheader()
        for row in rows_to_append:
            writer.writerow(row)

    print(f"[INFO] Added {len(rows_to_append)} row(s) to Weather_history.csv")


if __name__ == "__main__":
    print("[DEBUG] __main__ section starting")
    main()
    print("[DEBUG] run_once.py finished")
