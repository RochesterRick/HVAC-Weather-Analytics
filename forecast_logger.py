#!/usr/bin/env python3
import os, json, sqlite3, requests, csv, time
from datetime import datetime

BASE = os.path.dirname(__file__)
CONFIG = os.path.join(BASE, "config.json")
DB = os.path.join(BASE, "forecast_history.db")
FORECAST_CSV = os.path.join(BASE, "forecast_snapshots.csv")
FORECAST_CSV_FIELDS = [
    "snapshot_time", "target_date", "days_ahead",
    "pred_high", "pred_low", "condition",
]

def weather_code_text(code):
    if code is None:
        return ""
    labels = {
        0: "Sunny", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Fog",
        51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
        56: "Freezing drizzle", 57: "Freezing drizzle",
        61: "Rain", 63: "Rain", 65: "Rain",
        66: "Freezing rain", 67: "Freezing rain",
        71: "Snow", 73: "Snow", 75: "Snow", 77: "Snow",
        80: "Rain showers", 81: "Rain showers", 82: "Rain showers",
        85: "Snow showers", 86: "Snow showers",
        95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
    }
    try:
        return labels.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"

def append_forecast_csv(snapshot_time, dates, highs, lows, codes):
    new_file = not os.path.exists(FORECAST_CSV)
    with open(FORECAST_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FORECAST_CSV_FIELDS)
        if new_file:
            w.writeheader()
        for i, d in enumerate(dates):
            code = codes[i] if i < len(codes) else None
            hi = highs[i] if i < len(highs) else None
            lo = lows[i] if i < len(lows) else None
            w.writerow({
                "snapshot_time": snapshot_time,
                "target_date": d,
                "days_ahead": i,
                "pred_high": hi if hi is not None else "",
                "pred_low": lo if lo is not None else "",
                "condition": weather_code_text(code),
            })

def hours_ahead_from_times(pulled_at, forecast_time):
    pulled_dt = datetime.fromisoformat(pulled_at)
    forecast_dt = datetime.fromisoformat(forecast_time)
    delta_hours = (forecast_dt - pulled_dt).total_seconds() / 3600.0
    return max(0, int(delta_hours + 0.5))

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

def _request_json(url, timeout=20):
    headers = {"User-Agent": "HVACpi/1.0"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _fetch_open_meteo():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code"
        "&hourly=temperature_2m,weather_code"
        "&temperature_unit=fahrenheit"
        "&timezone=auto"
        "&forecast_days=10"
    )
    return _request_json(url, timeout=20)

def _fetch_weather_gov():
    points = _request_json(
        f"https://api.weather.gov/points/{LAT},{LON}",
        timeout=20,
    )
    forecast_url = points["properties"]["forecast"]
    forecast_data = _request_json(forecast_url, timeout=20)
    periods = forecast_data.get("properties", {}).get("periods", [])
    if not periods:
        raise RuntimeError("weather.gov returned no daily periods")

    daily_by_date = {}
    for period in periods:
        start = period.get("startTime")
        temp = period.get("temperature")
        name = (period.get("name") or "").lower()
        is_daytime = period.get("isDaytime")
        if not start or temp is None:
            continue
        dt = datetime.fromisoformat(start)
        date_key = dt.date().isoformat()
        daily = daily_by_date.setdefault(date_key, {"high": None, "low": None})
        if is_daytime:
            daily["high"] = temp if daily["high"] is None else max(daily["high"], temp)
        else:
            daily["low"] = temp if daily["low"] is None else min(daily["low"], temp)

    dates = sorted(d for d, vals in daily_by_date.items() if vals["high"] is not None or vals["low"] is not None)[:10]
    return {
        "daily": {
            "time": dates,
            "temperature_2m_max": [daily_by_date[d]["high"] for d in dates],
            "temperature_2m_min": [daily_by_date[d]["low"] for d in dates],
            "weather_code": [None for _ in dates],
        },
    }

def fetch_forecast():
    last_error = None
    for attempt in range(3):
        try:
            return _fetch_open_meteo()
        except Exception as exc:
            last_error = exc
            print(f"Open-Meteo forecast attempt {attempt + 1} failed: {exc}")
            time.sleep(5 * (attempt + 1))

    print(f"Open-Meteo unavailable; using weather.gov fallback: {last_error}")
    return _fetch_weather_gov()

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

    append_forecast_csv(pulled_at, dates, highs, lows, codes)

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    hcodes = hourly.get("weather_code", [])

    # Save first 48 hours hourly for detailed 24h forecast accuracy analysis.
    for i, t in enumerate(times[:48]):
        cur.execute("""
            INSERT INTO hourly_forecast
            (snapshot_id, forecast_time, hours_ahead, temp_f, weather_code)
            VALUES (?, ?, ?, ?, ?)
        """, (
            snapshot_id,
            t,
            hours_ahead_from_times(pulled_at, t),
            temps[i] if i < len(temps) else None,
            hcodes[i] if i < len(hcodes) else None
        ))

    con.commit()
    con.close()

    print(f"Saved forecast snapshot {snapshot_id} at {pulled_at}")

if __name__ == "__main__":
    main()
