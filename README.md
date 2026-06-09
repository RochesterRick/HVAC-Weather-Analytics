# HVAC Weather Analytics

A Raspberry Pi-based dashboard for tracking my home HVAC activity alongside local weather and forecast data.

This project is still a work in progress and is built around my own home setup. It runs locally on the Pi and is meant to help me understand how the furnace, fan, outdoor weather, and forecast accuracy relate to each other over time.
<img width="1279" height="636" alt="furnace" src="https://github.com/user-attachments/assets/bfb72197-f703-4044-a104-582a153100ba" />


## What It Does

- Tracks indoor temperature, outdoor temperature, furnace state, and fan state
- Logs HVAC readings to CSV files
- Pulls daily weather and forecast data
- Stores forecast history in SQLite and CSV files
- Compares past forecasts against actual weather readings
- Shows simple local web pages for HVAC status, costs, forecasts, and forecast accuracy

### How It Works

The Raspberry Pi monitors the HVAC system using a small AC optocoupler module connected to the furnace control circuit.

<img width="838" height="455" alt="Screenshot 2025-11-07 224133" src="https://github.com/user-attachments/assets/2f356106-08fd-4c81-b577-abbfc809d286" />

The optocoupler module was modified by replacing the input resistor with a lower-value resistor (From 100K to 10K) so it would reliably trigger from the furnace's 24 VAC control signal instead of the higher AC voltage it was originally designed for.

When the thermostat calls for heat, the furnace energizes the control signal. The optocoupler safely isolates the furnace voltage from the Raspberry Pi and provides a simple on/off signal that can be read through a GPIO pin.

The Raspberry Pi runs several Python scripts:

- A meter script monitors the HVAC state, temperatures, and other inputs, then writes readings to `meter.csv`
- A Flask web application reads the saved data and displays charts and status pages in a browser
- Forecast scripts download weather forecast data and save snapshots over time
- Accuracy scripts compare historical forecasts against the weather that actually occurred

Most of the project uses plain CSV files for easy inspection and troubleshooting, while SQLite is used for long-term forecast history storage.

### Hardware

- Raspberry Pi
- AC optocoupler module for furnace state detection
- Temperature sensors
- Local network connection for dashboard access

## Main Features

- Local Flask dashboard for viewing HVAC activity
- Indoor and outdoor temperature tracking
- Furnace and fan state tracking
- Cost-related pages based on saved rate settings
- Forecast history logging
- Forecast vs. actual weather comparison
- Chart views for recent HVAC and weather trends
- Runs locally without needing a hosted server

## Files and Components

- `web.py` - Flask web app and dashboard pages
- `meter.py` - Main HVAC/sensor logging script
- `forecast_logger.py` - Saves forecast snapshots
- `daily_weather.py` - Builds daily weather history from logged dates
- `build_forecast_accuracy.py` - Creates forecast-vs-actual comparison data
- `config.json` - Location, sensor, file, and device settings
- `rates.json` - Heating, cooling, blower, and electric cost settings
- `meter.csv` - Main HVAC reading log
- `Weather_history.csv` - Daily weather history
- `forecast_history.db` - SQLite database for forecast snapshots
- `forecast_snapshots.csv` - CSV copy of saved forecast snapshots
- `forecast_vs_actual_24h.csv` - Precomputed forecast accuracy output
- `restart_hvac.sh` - Helper script for restarting the local services
- `static/` - Static web assets such as the favicon
- `old/` - Older versions and experiments kept for reference

## Basic Setup and Run Notes

This project expects to run on a Raspberry Pi with the needed Python packages, sensor wiring, and local configuration already in place.

Basic flow:

1. Create or activate a Python virtual environment.
2. Install the Python dependencies used by the scripts, such as Flask, requests, gpiozero, Adafruit ADS1x15 support, and tinytuya.
3. Update `config.json` for your location, sensor settings, GPIO pin, and fan device.
4. Update `rates.json` with local cost assumptions.
5. Run the logger and web app:

```bash
python3 meter.py
python3 web.py
```

The included restart helper can also be used on the Pi:

```bash
./restart_hvac.sh
```

The dashboard is intended to be viewed from the local network, usually at the Pi's address and the Flask port used by `web.py`.

## Why I Built It

I wanted a simple way to see what my HVAC system was doing over time and compare that against outdoor weather. The goal is not to build a polished product. It is a practical home dashboard that helps answer questions like:

- How often is the furnace running?
- What is the indoor temperature doing over the day?
- How does outdoor weather affect HVAC use?
- Are recent forecasts close to what actually happened?
- What might the heating or blower costs look like?

## Future Ideas

- Cleaner setup instructions
- Better service/install documentation for a fresh Raspberry Pi
- More forecast accuracy views
- More reliable long-term data cleanup or archiving
- Better mobile layout for the dashboard
- Optional export or backup tools
- More notes on wiring and hardware assumptions
