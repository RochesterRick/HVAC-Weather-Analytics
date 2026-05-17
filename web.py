# web.py
from flask import Flask, jsonify, render_template_string, request
import os, json, csv, re
from datetime import datetime, timedelta

BASE = os.path.dirname(__file__)
CSV = os.path.join(BASE, "meter.csv")
STATE = os.path.join(BASE, "state.json")
CTRL = os.path.join(BASE, "control.json")
WEATHER_CSV = os.path.join(BASE, "Weather_history.csv")

app = Flask(__name__)

# --- no-cache headers so the browser always grabs fresh /state and /data ---
@app.after_request
def add_nocache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

HTML = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>HVAC Meter</title>
<style>
:root{--bg:#0b0f14;--panel:#0e141b;--line:#22303c;--text:#e6ebf0;--muted:#9fb3c8;}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);display:flex;align-items:center;gap:8px}
main{padding:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:0 6px 18px rgba(0,0,0,.35);margin-bottom:12px}
.btn{
  background:#13202d;
  color:#d7e4ef;
  border:1.5px solid #294457;
  border-radius:999px;
  padding:10px 18px;
  font-size:17px;
  font-weight:600;
  min-height:44px;
  cursor:pointer;
  text-decoration:none;
  display:inline-flex;
  align-items:center;
  justify-content:center;
}
.btn[disabled]{
  opacity:.45;
  cursor:not-allowed;
}
.badge{
  font-size:17px;
  font-weight:600;
  padding:8px 16px;
  border-radius:999px;
  border:1.5px solid #2a3a49;
  color:#b8c7d6;
  display:inline-flex;
  align-items:center;
  min-height:42px;
}
.toolbar{
  display:flex;
  gap:12px;
  align-items:center;
  flex-wrap:wrap;
}
.chart-box{height:520px;max-height:80vh}
canvas{width:100% !important;height:100% !important;display:block}
.spacer{flex:1}
</style></head><body>
<header>
  <h3 style="margin:0;font-size:22px;font-weight:700;">HVAC Meter</h3>
  <span class="spacer"></span>
  <span class="badge" id="lastupdate">Updated: --:--:--</span>
  <a class="btn" href="/costs">Costs</a>
  <a class="btn" href="/forecast">Forecast</a>
</header>
<main>
  <!-- Mode + live state -->
  <div class="card">
    <div class="toolbar">
      <span class="badge" id="heat">Heat: --</span>
      <span class="badge" id="fan">Fan: --</span>
      <span class="badge" id="inside">Inside: --</span>
      <span class="badge" id="outside">Outside: --</span>
      <span class="badge" id="mode">Mode: --</span>
      <span class="spacer"></span>

      <!-- Mode switch -->
      <button class="btn" id="btn-auto">Automatic (Follow)</button>
      <button class="btn" id="btn-manual">Manual</button>

      <!-- Manual controls (enabled only in Manual) -->
      <button class="btn" id="on">Fan ON</button>
      <button class="btn" id="off">Fan OFF</button>
      <!-- Boost removed -->
    </div>
  </div>

  <!-- Chart -->
  <div class="card">
    <div class="toolbar" style="margin-bottom:8px">
      <span style="font-size:16px;font-weight:600;color:#9fb3c8;">Window:</span>
      <button class="btn" data-h="12">12h</button>
      <button class="btn" data-h="24">24h</button>
      <button class="btn" data-h="48">48h</button>
      <button class="btn" data-h="168">1w</button>
    </div>
    <div class="chart-box"><canvas id="c"></canvas></div>
  </div>
</main>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<script>
let hours=24, raw=null, chart=null;

function el(id){return document.getElementById(id)}
async function getState(){ const r = await fetch('/state?_=' + Date.now(), {cache:'no-store'}); return await r.json(); }
async function cmd(body){ await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }

// --- stamp only the time (no date) ---
function stampNow(){
  const e = el('lastupdate'); if(!e) return;
  const d = new Date();
  const t = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  e.textContent = 'Updated: ' + t;
}

function setManualButtonsEnabled(enabled){
  ['on','off'].forEach(id=>{
    const b = el(id);
    if(!b) return;
    if(enabled){ b.removeAttribute('disabled'); }
    else{ b.setAttribute('disabled',''); }
  });
}

async function refreshBadges(){
  const s = await getState();

  // badges
  el('heat').textContent   = 'Heat: ' + (s.furnace_on? 'ON':'OFF');
  el('fan').textContent    = 'Fan: ' + (s.fan_on? 'ON':'OFF');
  el('inside').textContent = 'Inside: ' + (s.indoor_f!=null ? s.indoor_f.toFixed(1)+'°F':'--');
  el('outside').textContent= 'Outside: ' + (s.outdoor_f!=null ? s.outdoor_f.toFixed(1)+'°F':'--');

  const auto = !!s.follow_heat;
  el('mode').textContent   = 'Mode: ' + (auto ? 'Automatic' : 'Manual');

  // mode button highlighting (optional; purely visual)
  el('btn-auto').style.opacity   = auto ? '1'   : '0.6';
  el('btn-manual').style.opacity = auto ? '0.6' : '1';

  // enable/disable manual controls based on mode
  setManualButtonsEnabled(!auto);
}

function sliceByHours(d,hrs){
  if(!d.ts_ms?.length) return d;
  const last=d.ts_ms[d.ts_ms.length-1] || Date.now();
  const from = last - hrs*3600*1000;
  let i=0; while(i<d.ts_ms.length && d.ts_ms[i]<from) i++;
  function sl(a){return a?a.slice(i):a}
  return {labels:d.labels.slice(i), ts_ms:d.ts_ms.slice(i), inside:sl(d.inside), outside:sl(d.outside), heat01:sl(d.heat01)};
}

function draw(view){
  if(chart) chart.destroy();
  chart = new Chart(document.getElementById('c').getContext('2d'), {
    data:{
      labels:view.labels,
      datasets:[
        {type:'bar', label:'Heat (0/100)', data:view.heat01, yAxisID:'y', borderWidth:0, borderRadius:3, barPercentage:0.95, categoryPercentage:1.0},
        {type:'line', label:'Inside (°F)', data:view.inside, yAxisID:'y', tension:0.25, pointRadius:0, borderWidth:2},
        {type:'line', label:'Outside (°F)', data:view.outside, yAxisID:'y', tension:0.25, pointRadius:0, borderWidth:2}
      ]
    },
    options:{
  maintainAspectRatio:false,
  interaction:{mode:'index', intersect:false},
  scales:{
    x:{
      ticks:{
        autoSkip: true,
        maxTicksLimit: 8,   // <= about 8 labels max, even for 1 week
        maxRotation: 0,
        minRotation: 0,
        callback: function(value){
          const label = this.getLabelForValue(value); // ISO timestamp
          // Show as "11-16 07:35"
          return label.replace('T',' ').slice(5,16);
        }
      }
    },
    y:{
      min:-20,
      max:100,
      title:{display:true,text:'Scale (°F) and Heat 0/100'}
    }
  }
}

  });
}

async function loadData(){
  const r = await fetch('/data?_=' + Date.now(), {cache:'no-store'});
  raw = await r.json();
  draw(sliceByHours(raw, hours));
}

// window buttons
document.querySelectorAll('.btn[data-h]').forEach(b=> b.onclick=()=>{hours=parseInt(b.dataset.h,10); draw(sliceByHours(raw,hours))});

// MODE buttons
el('btn-auto').onclick   = async () => {
  await cmd({"follow_heat": true});
  await refreshBadges();
  stampNow();
};
el('btn-manual').onclick = async () => {
  await cmd({"follow_heat": false});
  await refreshBadges();
  stampNow();
};

// Manual controls (only enabled in Manual)
el('on').onclick  = async () => {
  if(el('on').disabled) return;
  await cmd({"fan":"on"});
  await refreshBadges();
  stampNow();
};
el('off').onclick = async () => {
  if(el('off').disabled) return;
  await cmd({"fan":"off"});
  await refreshBadges();
  stampNow();
};

// run once now, then every 60s: refresh badges + reload /data (chart) + stamp time
async function loop() {
  try {
    await refreshBadges();
    await loadData();
    stampNow();
  } catch(e) {
    console.error(e);
  } finally {
    setTimeout(loop, 60000); // 1 minute
  }
}

(async()=>{ await loadData(); await refreshBadges(); stampNow(); loop(); })();
</script>
</body></html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/state")
def state():
    try:
        with open(STATE) as f: s = json.load(f)
    except Exception:
        s = {}
    return jsonify(s)

@app.route("/control", methods=["POST"])
def control():
    try:
        payload = request.get_json(force=True) or {}
        with open(CTRL, "w") as f: json.dump(payload, f)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "err": str(e)}), 500

# ---------- CSV → chart payload ----------
_num = re.compile(r"[-+]?\d+(?:\.\d+)?")
def fnum(x):
    if x is None: return None
    m = _num.search(str(x));
    if not m: return None
    try: return float(m.group(0))
    except: return None

def ts_ms(s):
    try: return int(datetime.fromisoformat(s).timestamp()*1000)
    except Exception: return None

def load_weather_history():
    """Return dict: date -> {avg_f, high_f, low_f} from Weather_history.csv."""
    data = {}
    if not os.path.exists(WEATHER_CSV):
        return data
    with open(WEATHER_CSV, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            d = row.get("date")
            if not d:
                continue
            try:
                avg = float(row.get("avg_f")) if row.get("avg_f") not in (None, "") else None
                hi  = float(row.get("high_f")) if row.get("high_f") not in (None, "") else None
                lo  = float(row.get("low_f")) if row.get("low_f") not in (None, "") else None
            except Exception:
                avg = hi = lo = None
            data[d] = {"avg_f": avg, "high_f": hi, "low_f": lo}
    return data

@app.route("/data")
def data():
    if not os.path.exists(CSV):
        return jsonify({"labels":[],"ts_ms":[],"inside":[],"outside":[],"heat01":[]})
    labels, tms, inside, outside, heat01 = [], [], [], [], []
    with open(CSV, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = row.get("timestamp_iso",""); labels.append(ts)
            tms.append(ts_ms(ts) or 0)
            inside.append(fnum(row.get("indoor_f")))
            outside.append(fnum(row.get("outdoor_f")))
            heat01.append(100 if (fnum(row.get("furnace_on")) or 0)>0 else 0)
    return jsonify({"labels":labels,"ts_ms":tms,"inside":inside,"outside":outside,"heat01":heat01})

# ---------- Costs page ----------
COSTS_HTML = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>HVAC Costs</title>
<style>
:root{--bg:#0b0f14;--panel:#0e141b;--line:#22303c;--text:#e6ebf0;--muted:#9fb3c8;}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);display:flex;align-items:center;gap:8px}
main{padding:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;box-shadow:0 6px 18px rgba(0,0,0,.35);margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th,td{padding:8px;border-bottom:1px solid #22303c;font-size:14px}
th{color:#b8c7d6;text-align:left}
.num{text-align:right}
.btn{background:#13202d;color:#d7e4ef;border:1px solid #294457;border-radius:999px;padding:6px 10px;font-size:12px;cursor:pointer;text-decoration:none;display:inline-block}
.spacer{flex:1}
</style></head><body>
<header>
  <h3 style="margin:0;">HVAC Costs</h3>
  <span class="spacer"></span>
  <a class="btn" href="/">HVAC</a>
  <a class="btn" href="/forecast">Forecast</a>
</header>
<main>
  <div class="card">
    <div style="font-size:12px;color:#9fb3c8;margin-bottom:8px;">Last {{days}} days</div>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th class="num">Runtime (min)</th>
          <th class="num">Gas ($)</th>
          <th class="num">Electric ($)</th>
          <th class="num">Total ($)</th>
          <th class="num">Avg (°F)</th>
          <th class="num">High (°F)</th>
          <th class="num">Low (°F)</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr>
          <td>{{row["date"]}}</td>
          <td class="num">{{row["runtime_min"]}}</td>
          <td class="num">{{"%.2f"|format(row["gas_usd"])}}</td>
          <td class="num">{{"%.2f"|format(row["elec_usd"])}}</td>
          <td class="num">{{"%.2f"|format(row["total_usd"])}}</td>
          <td class="num">
                  {% if row["avg_f"] is not none %}{{"%.0f"|format(row["avg_f"])}}{% else %}--{% endif %}
                </td>
                <td class="num">
                  {% if row["high_f"] is not none %}{{"%.0f"|format(row["high_f"])}}{% else %}--{% endif %}
                </td>
                <td class="num">
                  {% if row["low_f"] is not none %}{{"%.0f"|format(row["low_f"])}}{% else %}--{% endif %}
                </td>

        </tr>
        {% endfor %}
      </tbody>
      <tfoot>
        <tr>
          <th>Total</th>
          <th class="num">{{totals["runtime_min"]}}</th>
          <th class="num">{{"%.2f"|format(totals["gas_usd"])}}</th>
          <th class="num">{{"%.2f"|format(totals["elec_usd"])}}</th>
          <th class="num">{{"%.2f"|format(totals["total_usd"])}}</th>
          <th class="num">--</th>
          <th class="num">--</th>
          <th class="num">--</th>
        </tr>
      </tfoot>
    </table>
  </div>
</main>
</body></html>
"""

def parse_date_local(ts):
    try:
        return datetime.fromisoformat(ts).date().isoformat()
    except Exception:
        return None

@app.route("/costs")
def costs():
    days = 7
    if not os.path.exists(CSV):
        return render_template_string(
            COSTS_HTML,
            days=days,
            rows=[],
            totals={"runtime_min":0,"gas_usd":0.0,"elec_usd":0.0,"total_usd":0.0}
        )

    by_day = {}
    with open(CSV, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = row.get("timestamp_iso","")
            d = parse_date_local(ts)
            if not d:
                continue
            gas_c = fnum(row.get("gas_cost_cents")) or 0.0
            elec_c = fnum(row.get("elec_cost_cents")) or 0.0
            # crude runtime: 1 minute if furnace burned gas this minute
            runtime_min = 1 if gas_c > 0 else 0

            entry = by_day.setdefault(d, {"runtime_min":0, "gas_c":0.0, "elec_c":0.0})
            entry["runtime_min"] += runtime_min
            entry["gas_c"] += gas_c
            entry["elec_c"] += elec_c

    # Load weather data and join by date
    weather = load_weather_history()

    dates_sorted = sorted(by_day.keys(), reverse=True)[:days]
    rows = []
    totals = {"runtime_min":0, "gas_usd":0.0, "elec_usd":0.0, "total_usd":0.0}

    for d in sorted(dates_sorted):
        e = by_day[d]
        gas_usd = e["gas_c"] / 100.0
        elec_usd = e["elec_c"] / 100.0
        total_usd = gas_usd + elec_usd

        w = weather.get(d, {})
        avg_f = w.get("avg_f")
        high_f = w.get("high_f")
        low_f = w.get("low_f")

        rows.append({
            "date": d,
            "runtime_min": e["runtime_min"],
            "gas_usd": gas_usd,
            "elec_usd": elec_usd,
            "total_usd": total_usd,
            "avg_f": avg_f,
            "high_f": high_f,
            "low_f": low_f
        })

        totals["runtime_min"] += e["runtime_min"]
        totals["gas_usd"] += gas_usd
        totals["elec_usd"] += elec_usd   # <- fixed bug here
        totals["total_usd"] += total_usd

    return render_template_string(COSTS_HTML, days=days, rows=rows, totals=totals)

# ---------- Forecast page ----------

FORECAST_DB = os.path.join(BASE, "forecast_history.db")

def weather_code_text(code):
    """Open-Meteo WMO weather_code -> short label."""
    if code is None:
        return "—"
    labels = {
        0: "Sunny",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Fog",
        51: "Drizzle",
        53: "Drizzle",
        55: "Drizzle",
        56: "Freezing drizzle",
        57: "Freezing drizzle",
        61: "Rain",
        63: "Rain",
        65: "Rain",
        66: "Freezing rain",
        67: "Freezing rain",
        71: "Snow",
        73: "Snow",
        75: "Snow",
        77: "Snow",
        80: "Rain showers",
        81: "Rain showers",
        82: "Rain showers",
        85: "Snow showers",
        86: "Snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm",
        99: "Thunderstorm",
    }
    try:
        return labels.get(int(code), "Unknown")
    except (TypeError, ValueError):
        return "Unknown"

FORECAST_HTML = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Forecast Analysis</title>

<style>
:root{
  --bg:#0b0f14;
  --panel:#0e141b;
  --line:#22303c;
  --text:#e6ebf0;
}

body{
  margin:0;
  background:var(--bg);
  color:var(--text);
  font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif
}

header{
  padding:12px 16px;
  border-bottom:1px solid var(--line);
  display:flex;
  align-items:center;
  gap:8px
}

main{padding:16px}

.card{
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:12px;
  padding:12px;
  margin-bottom:12px
}

table{
  width:100%;
  border-collapse:collapse
}

th,td{
  padding:10px;
  border-bottom:1px solid #22303c;
  text-align:left
}

.btn{
  background:#13202d;
  color:#d7e4ef;
  border:1px solid #294457;
  border-radius:999px;
  padding:6px 12px;
  text-decoration:none
}

.spacer{flex:1}
</style>
</head>
<body>

<header>
  <h3 style="margin:0;">Forecast Analysis</h3>
  <span class="spacer"></span>
  <a class="btn" href="/">HVAC</a>
  <a class="btn" href="/costs">Costs</a>
</header>

<main>

<div class="card">
  <h3>Latest Forecast Snapshot</h3>
  <p style="margin:0 0 12px;color:#9fb3c8;font-size:14px;">Snapshot: {{ snapshot_time }}</p>

  <table>
    <thead>
      <tr>
        <th>Day Ahead</th>
        <th>Date</th>
        <th>High</th>
        <th>Low</th>
        <th>Condition</th>
      </tr>
    </thead>

    <tbody>
    {% for row in rows %}
      <tr>
        <td>{{row.days_ahead}}</td>
        <td>{{row.forecast_date}}</td>
        <td>{{row.temp_high_f}}°F</td>
        <td>{{row.temp_low_f}}°F</td>
        <td>{{ weather_code_text(row.weather_code) }}</td>
      </tr>
    {% endfor %}
    </tbody>

  </table>
</div>

<div class="card">
  <h3>Forecast Accuracy</h3>
  {% if accuracy_rows %}
  <table>
    <thead>
      <tr>
        <th>Snapshot</th>
        <th>Date</th>
        <th>Day Ahead</th>
        <th>Pred High</th>
        <th>Actual High</th>
        <th>High Error</th>
        <th>Pred Low</th>
        <th>Actual Low</th>
        <th>Low Error</th>
      </tr>
    </thead>
    <tbody>
    {% for row in accuracy_rows %}
      <tr>
        <td>{{ row.pulled_at }}</td>
        <td>{{ row.forecast_date }}</td>
        <td>{{ row.days_ahead }}</td>
        <td>{{ row.pred_high }}</td>
        <td>{{ row.actual_high }}</td>
        <td>{{ row.high_error }}</td>
        <td>{{ row.pred_low }}</td>
        <td>{{ row.actual_low }}</td>
        <td>{{ row.low_error }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="margin:0;color:#9fb3c8;font-size:14px;">No saved forecasts overlap with dates in Weather_history.csv yet.</p>
  {% endif %}
</div>

</main>
</body></html>
"""

@app.route("/forecast")
def forecast():

    import sqlite3

    if not os.path.exists(FORECAST_DB):
        return "forecast_history.db not found"

    con = sqlite3.connect(FORECAST_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT id, pulled_at
        FROM forecast_snapshots
        ORDER BY id DESC
        LIMIT 1
    """)

    latest = cur.fetchone()

    if not latest:
        return "No forecast snapshots yet"

    snapshot_id = latest["id"]
    snapshot_time = latest["pulled_at"]

    cur.execute("""
        SELECT
            forecast_date,
            days_ahead,
            temp_high_f,
            temp_low_f,
            weather_code
        FROM daily_forecast
        WHERE snapshot_id=?
        ORDER BY days_ahead
    """, (snapshot_id,))

    rows = cur.fetchall()

    actuals = load_weather_history()
    cur.execute("""
        SELECT
            s.pulled_at,
            d.forecast_date,
            d.days_ahead,
            d.temp_high_f,
            d.temp_low_f
        FROM daily_forecast d
        JOIN forecast_snapshots s ON d.snapshot_id = s.id
        ORDER BY d.forecast_date DESC, s.pulled_at DESC, d.days_ahead
    """)
    def _temp(v):
        return "—" if v is None else f"{v:.1f}°F"
    def _err(v):
        return "—" if v is None else f"{v:+.1f}°F"

    accuracy_rows = []
    for r in cur.fetchall():
        act = actuals.get(r["forecast_date"])
        if not act:
            continue
        ph, pl = r["temp_high_f"], r["temp_low_f"]
        ah, al = act["high_f"], act["low_f"]
        he = (ph - ah) if ph is not None and ah is not None else None
        le = (pl - al) if pl is not None and al is not None else None
        accuracy_rows.append({
            "pulled_at": r["pulled_at"],
            "forecast_date": r["forecast_date"],
            "days_ahead": r["days_ahead"],
            "pred_high": _temp(ph),
            "actual_high": _temp(ah),
            "high_error": _err(he),
            "pred_low": _temp(pl),
            "actual_low": _temp(al),
            "low_error": _err(le),
        })

    con.close()

    return render_template_string(
        FORECAST_HTML,
        rows=rows,
        snapshot_time=snapshot_time,
        weather_code_text=weather_code_text,
        accuracy_rows=accuracy_rows,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=False)
