#!/usr/bin/env python3
import os, json, sqlite3, requests
from datetime import datetime

BASE = os.path.dirname(__file__)
CONFIG = os.path.join(BASE, "config.json")
DB = os.path.join(BASE, "forecast_history.db")

with open(CONFIG) as f:
    CFG = json.load(f)

LAT = CFG["location"]["lat"]
LON = CFG["location"]["lon"]

def connect_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS forecast_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pulled_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_forecast (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER,
        forecast_date TEXT,
        days_ahead INTEGER,
        temp_high_f REAL,
        temp_low_f REAL,
        weather_code INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hourly_forecast (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER,
        forecast_time TEXT,
        hours_ahead INTEGER,
        temp_f REAL,
        weather_code INTEGER
    )
    """)

    con.commit()
    return con

def fetch_forecast():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code"
        "&hourly=temperature_2m,weather_code"
        "&temperature_unit=fahrenheit"
        "&timezone=auto"
        "&forecast_days=10"
    )

    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def main():
    pulled_at = datetime.now().isoformat(timespec="seconds")
    data = fetch_forecast()

    con = connect_db()
    cur = con.cursor()

    cur.execute("INSERT INTO forecast_snapshots (pulled_at) VALUES (?)", (pulled_at,))
    snapshot_id = cur.lastrowid

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])

    for i, d in enumerate(dates):
        cur.execute("""
            INSERT INTO daily_forecast
            (snapshot_id, forecast_date, days_ahead, temp_high_f, temp_low_f, weather_code)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            snapshot_id,
            d,
            i,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            codes[i] if i < len(codes) else None
        ))

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    hcodes = hourly.get("weather_code", [])

    # Save first 48 hours hourly for detailed day 1/day 2 analysis
    for i, t in enumerate(times[:48]):
        cur.execute("""
            INSERT INTO hourly_forecast
            (snapshot_id, forecast_time, hours_ahead, temp_f, weather_code)
            VALUES (?, ?, ?, ?, ?)
        """, (
            snapshot_id,
            t,
            i,
            temps[i] if i < len(temps) else None,
            hcodes[i] if i < len(hcodes) else None
        ))

    con.commit()
    con.close()

    print(f"Saved forecast snapshot {snapshot_id} at {pulled_at}")

if __name__ == "__main__":
    main()