#!/usr/bin/env python3
import os, sys, json, csv, time, math, threading, fcntl
from datetime import datetime, timedelta

BASE = os.path.dirname(__file__)
def pjoin(x): return os.path.join(BASE, x)

# ---- singleton (prevent multiple meter.py instances)
_lockf = None
try:
    _lockf = open("/tmp/hvac_meter.lock", "w")
    fcntl.flock(_lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lockf.write(str(os.getpid()))
    _lockf.flush()
except Exception:
    print("[FATAL] Another meter.py is already running. Exiting.")
    sys.exit(1)

# ------------ Load config & rates ------------
with open(pjoin("config.json")) as f: CFG = json.load(f)
with open(pjoin("rates.json")) as f: RATE = json.load(f)

LAT = CFG["location"]["lat"]; LON = CFG["location"]["lon"]
FAST_SEC = CFG["poll"]["fast_sec"]; MINUTE_SEC = CFG["poll"]["minute_sec"]; DEBOUNCE_SEC = CFG["poll"]["debounce_sec"]
CSV_PATH = pjoin(CFG["files"]["csv"]); STATE_PATH = pjoin(CFG["files"]["state"]); CTRL_PATH = pjoin(CFG["files"]["control"])

ADS_C = CFG["indoor_sensor"]["ads_channel"]; V_SUP = CFG["indoor_sensor"]["v_supply"]
R_FIXED = CFG["indoor_sensor"]["r_fixed"]; R0 = CFG["indoor_sensor"]["r0"]; BETA = CFG["indoor_sensor"]["beta"]
T0K = (CFG["indoor_sensor"]["t0_c"] + 273.15); ALPHA = CFG["indoor_sensor"]["ema_alpha"]

FURNACE_GPIO = CFG["furnace_gpio"]
FAN = CFG["fan"]["tuya"]; FOLLOW_HEAT_DEFAULT = bool(CFG["fan"].get("follow_heat", True))
DPS = str(FAN.get("dps","1"))
DPS_I = int(FAN.get("dps", 1))  # int index as primary

H_RATE = RATE["heating"]; BLOWER = RATE["blower"]; ELEC = RATE["electric"]; COOL = RATE["cooling"]

# ------------ Lazy deps ------------
_have_ads = _have_gpio = _have_requests = _have_tuya = False
ads = ch = None; ema_v = None

try:
    import board, busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    _have_ads = True
except Exception as e:
    print("[WARN] ADS1115 not available:", e)

try:
    from gpiozero import Button
    _have_gpio = True
except Exception as e:
    print("[WARN] gpiozero not available:", e)

try:
    import requests
    _have_requests = True
except Exception as e:
    print("[WARN] requests not available:", e)

try:
    import tinytuya
    _have_tuya = True
except Exception as e:
    print("[WARN] tinytuya not available:", e)

# ------------ Hardware init ------------
if _have_ads:
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c); ads.gain = 1; ads.data_rate = 128
        try:
            ch = AnalogIn(ads, ADS.P0 + ADS_C)
        except Exception:
            ch = AnalogIn(ads, ADS_C)
    except Exception as e:
        print("[WARN] ADS init failed:", e); ads = ch = None

furn_btn = None
if _have_gpio:
    try:
        # Active LOW (opto pulls to GND when furnace/AC call is ON)
        furn_btn = Button(FURNACE_GPIO, pull_up=True, bounce_time=None)  # manual debounce
    except Exception as e:
        print("[WARN] GPIO init failed:", e); furn_btn = None

dev = None
def tuya_dev():
    global dev
    if not _have_tuya: return None
    if dev is None:
        dev = tinytuya.OutletDevice(FAN["device_id"], FAN["ip"], FAN["local_key"])
        # version must be numeric
        try:
            ver = float(FAN.get("version", 3.3))
        except Exception:
            ver = 3.3
        dev.set_version(ver)
        dev.set_socketPersistent(True)
    return dev

# ------------ Helpers ------------
def c_to_f(c):
    try:
        return c*9/5+32
    except:
        return None

def ensure_csv():
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH)==0:
        with open(CSV_PATH, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp_iso","indoor_f","outdoor_f","mode",
                "furnace_on","ac_on","fan_on","gas_cost_cents","elec_cost_cents"
            ])

def read_indoor_c():
    global ema_v
    if ch is None: return float("nan")
    try:
        v = ch.voltage
        if v is None or v<=0.0 or v>=V_SUP: return float("nan")
        ema_v = v if ema_v is None else (ALPHA*v + (1-ALPHA)*ema_v)
        r_th = R_FIXED * (ema_v / (V_SUP - ema_v))
        if r_th<=0: return float("nan")
        tK = 1.0 / (1.0/T0K + (1.0/BETA)*math.log(r_th/R0))
        return tK - 273.15
    except Exception:
        return float("nan")

_last_outdoor_ts = 0
_cached_outdoor_c = float("nan")
def outdoor_c():
    global _last_outdoor_ts, _cached_outdoor_c
    now = time.time()
    if (now - _last_outdoor_ts) < 55 and not math.isnan(_cached_outdoor_c):
        return _cached_outdoor_c
    if not _have_requests: return float("nan")
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT:.5f}&longitude={LON:.5f}&current=temperature_2m&temperature_unit=celsius"
        r = requests.get(url, timeout=6); r.raise_for_status()
        _cached_outdoor_c = float(r.json()["current"]["temperature_2m"])
        _last_outdoor_ts = now
        return _cached_outdoor_c
    except Exception:
        return float("nan")

def fan_get():
    d = tuya_dev()
    if d is None: return None
    try:
        s = d.status()
        dps = (s.get("dps") or {})
        cur = dps.get(DPS_I)
        if cur is None:
            cur = dps.get(DPS)
        return bool(cur)
    except Exception:
        try: d.close()
        except: pass
        time.sleep(0.2)
        try:
            s = tuya_dev().status()
            dps = (s.get("dps") or {})
            cur = dps.get(DPS_I)
            if cur is None:
                cur = dps.get(DPS)
            return bool(cur)
        except Exception:
            return None

# --- rate-limited, chatter-resistant fan control
_last_fan_desired = None
_last_fan_set_ts = 0.0
MIN_SET_INTERVAL = 1.5  # seconds between real set attempts

def fan_set(on):
    d = tuya_dev()
    if d is None: return False
    try:
        d.set_status(bool(on), DPS_I)  # write via int index
        # verify
        s = d.status()
        dps = (s.get("dps") or {})
        cur = dps.get(DPS_I)
        if cur is None:
            cur = dps.get(DPS)
        return bool(cur) == bool(on)
    except Exception:
        try: d.close()
        except: pass
        time.sleep(0.2)
        try:
            tuya_dev().set_status(bool(on), DPS_I)
            s = tuya_dev().status()
            dps = (s.get("dps") or {})
            cur = dps.get(DPS_I)
            if cur is None:
                cur = dps.get(DPS)
            return bool(cur) == bool(on)
        except Exception:
            return False

def fan_ensure(on):
    """Only send a command when desired changes and not too frequently."""
    global _last_fan_desired, _last_fan_set_ts
    desired = bool(on)

    # Skip repeats within interval
    if _last_fan_desired is not None and desired == _last_fan_desired:
        if (time.time() - _last_fan_set_ts) < MIN_SET_INTERVAL:
            return

    # If device already matches, don't command
    cur = fan_get()
    if cur is not None and bool(cur) == desired:
        _last_fan_desired = desired
        return

    # Rate limit
    if (time.time() - _last_fan_set_ts) < MIN_SET_INTERVAL:
        return

    if fan_set(desired):
        _last_fan_desired = desired
        _last_fan_set_ts = time.time()

def is_boost_active(iso_ts):
    """Return True only if boost_until is a valid ISO timestamp in the future."""
    if not iso_ts:
        return False
    try:
        return datetime.now() < datetime.fromisoformat(iso_ts)
    except Exception:
        return False

def compute_call_on(mode, stable_call):
    """stable_call==1 means there's a thermostat call on the sense line."""
    if mode == "heat":
        return bool(stable_call == 1)
    if mode == "cool":
        return bool(stable_call == 1)  # same sense line used; treat as AC call
    return False

# ------------ State ------------
state = {
    "ts": None,
    "mode": "heat",               # "heat" or "cool"
    "furnace_on": 0,
    "ac_on": 0,
    "fan_on": False,
    "follow_heat": FOLLOW_HEAT_DEFAULT,
    "boost_until": None,
    "indoor_f": None,
    "outdoor_f": None,
    "last_change": None
}
lock = threading.Lock()

def write_state():
    with lock:
        s = dict(state)
    with open(STATE_PATH, "w") as f:
        json.dump(s, f, indent=2)

def read_control():
    try:
        with open(CTRL_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def clear_control():
    try:
        os.remove(CTRL_PATH)
    except FileNotFoundError:
        pass

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def append_row(ts, indoor_f, outdoor_f, mode, f_on, ac_on, fan_on, gas_cents, elec_cents):
    with open(CSV_PATH, "a", newline="") as f:
        csv.writer(f).writerow([
            ts,
            "" if indoor_f is None else f"{indoor_f:.2f}",
            "" if outdoor_f is None else f"{outdoor_f:.2f}",
            mode,
            int(f_on), int(ac_on), int(bool(fan_on)),
            f"{gas_cents:.2f}", f"{elec_cents:.2f}"
        ])

# ------------ Cost calc ------------
def costs_for_minute(mode, furnace_on, ac_on):
    gas_c = 0.0; elec_c = 0.0
    # blower cost (both heat/cool if blower runs with call)
    if furnace_on or ac_on:
        kWh_blower = (BLOWER["watts"]/1000.0) * (1/60.0)
        elec_c += kWh_blower * ELEC["per_kwh_usd"] * 100.0
    if mode == "heat" and furnace_on:
        therms_per_hr = H_RATE["furnace_btu_input"]/100000.0
        gas_c += (therms_per_hr/60.0) * H_RATE["gas_per_therm_usd"] * 100.0
    if mode == "cool" and ac_on:
        if COOL["mode"] == "kw":
            kWh = float(COOL["condenser_kw"]) * (1/60.0)
        else:
            kW = float(COOL["capacity_btu_hr"]) / (float(COOL["eer"])*3412.0)
            kWh = kW * (1/60.0)
        elec_c += kWh * ELEC["per_kwh_usd"] * 100.0
    return gas_c, elec_c

# ------------ Fast loop (edges, fan, state, event rows) ------------
def fast_loop():
    stable = None
    stable_since = time.time()
    call_on_started_ts = 0.0  # when call became ON (confirmed by debounce)
    CALL_CONFIRM_SEC = max(1.0, float(DEBOUNCE_SEC))

    while True:
        # control channel from web
        ctl = read_control()
        if ctl:
            changed = False
            if "fan" in ctl:
                if ctl["fan"] == "on": fan_ensure(True); changed = True
                if ctl["fan"] == "off": fan_ensure(False); changed = True
            if "follow_heat" in ctl:
                with lock: state["follow_heat"] = bool(ctl["follow_heat"]); changed = True
            if "boost_minutes" in ctl:
                mins = max(1, int(ctl["boost_minutes"]))
                until = datetime.now() + timedelta(minutes=mins)
                with lock: state["boost_until"] = until.astimezone().isoformat(timespec="seconds")
                fan_ensure(True); changed = True
            if ctl.get("cancel_boost"):
                with lock: state["boost_until"] = None; changed = True
            if changed: write_state()
            clear_control()

        # read GPIO (active LOW → not pressed means ON)
        raw_on = 0
        if furn_btn is not None:
            raw_on = 1 if (not furn_btn.is_pressed) else 0  # LOW => call ON
        now = time.time()

        if stable is None:
            stable = raw_on; stable_since = now
        else:
            if raw_on != stable:
                # candidate change, wait debounce window
                if (now - stable_since) >= DEBOUNCE_SEC:
                    stable = raw_on; stable_since = now
                    # event: update state + log event row
                    with lock:
                        mode = state["mode"]
                        call_on = compute_call_on(mode, stable)
                        state["furnace_on"] = 1 if (mode=="heat" and call_on) else 0
                        state["ac_on"]      = 1 if (mode=="cool" and call_on) else 0
                        state["last_change"] = now_iso()

                    # track confirmed call time (already debounced)
                    if call_on:
                        call_on_started_ts = time.time()
                    else:
                        call_on_started_ts = 0.0

                    append_row(now_iso(), c_to_f(read_indoor_c()), None, mode, state["furnace_on"], state["ac_on"], fan_get(), 0.0, 0.0)

                    # follow if enabled (respect boost)
                    with lock:
                        follow = state["follow_heat"]; boost_until = state["boost_until"]
                    if is_boost_active(boost_until):
                        fan_ensure(True)  # active boost forces ON
                    elif follow:
                        fan_ensure(call_on)
                    # else manual mode: leave fan as-is
                    write_state()
            else:
                # no confirmed change; keep debounce clock updated
                stable_since = now

        # If boost is active AND a call has been ON long enough, cancel boost once
        with lock:
            bu = state["boost_until"]; follow = state["follow_heat"]; mode = state["mode"]
        call_on_now = compute_call_on(mode, stable)
        if is_boost_active(bu) and call_on_now and call_on_started_ts and (time.time() - call_on_started_ts) >= CALL_CONFIRM_SEC:
            with lock:
                state["boost_until"] = None
            # after cancel, enforce current policy
            want = call_on_now if follow else False
            fan_ensure(want)
            write_state()

        # keep state live (do not treat Tuya None as OFF)
        with lock:
            prev_on = state.get("fan_on", False)
        cur = fan_get()
        with lock:
            state["fan_on"] = prev_on if (cur is None) else bool(cur)
            state["ts"] = now_iso()
        write_state()

        # periodic enforcement (handles missed edges or Tuya hiccups)
        with lock:
            follow = state["follow_heat"]; boost_until = state["boost_until"]; mode = state["mode"]
        if is_boost_active(boost_until):
            fan_ensure(True)  # boost only ever forces ON
        elif follow:
            fan_ensure(compute_call_on(mode, stable))
        # else: manual mode; leave fan alone

        time.sleep(FAST_SEC)

# ------------ Minute logger (aligned) ------------
def minute_loop():
    ensure_csv()
    # align to clock
    while True:
        now = datetime.now()
        sleep_s = MINUTE_SEC - (now.second % MINUTE_SEC)
        time.sleep(sleep_s if sleep_s>0 else MINUTE_SEC)

        with lock:
            mode = state["mode"]; f_on = state["furnace_on"]; ac_on = state["ac_on"]
        ind_f = c_to_f(read_indoor_c()); out_f = c_to_f(outdoor_c())
        gas_c, elec_c = costs_for_minute(mode, f_on, ac_on)

        with lock:
            state["indoor_f"] = ind_f; state["outdoor_f"] = out_f
        append_row(now_iso(), ind_f, out_f, mode, f_on, ac_on, state.get("fan_on", False), gas_c, elec_c)
        write_state()

# ------------ Main ------------
if __name__ == "__main__":
    print("[INFO] hvac meter starting…")
    write_state()
    threading.Thread(target=fast_loop, daemon=True).start()
    minute_loop()
