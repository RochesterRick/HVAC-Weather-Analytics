# web.py
from flask import Flask, jsonify, render_template_string, request
import os, json, csv, re
from datetime import datetime, timedelta

BASE = os.path.dirname(__file__)
CSV = os.path.join(BASE, "meter.csv")
STATE = os.path.join(BASE, "state.json")
CTRL = os.path.join(BASE, "control.json")

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
.btn{background:#13202d;color:#d7e4ef;border:1px solid #294457;border-radius:999px;padding:6px 10px;font-size:12px;cursor:pointer;text-decoration:none;display:inline-block}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid #2a3a49;color:#b8c7d6}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chart-box{height:520px;max-height:80vh}
canvas{width:100% !important;height:100% !important;display:block}
.spacer{flex:1}
</style></head><body>
<header>
  <h3 style="margin:0;">HVAC Meter</h3>
  <span class="spacer"></span>
  <a class="btn" href="/costs">Costs</a>
</header>
<main>
  <div class="card">
    <div class="toolbar">
      <span class="badge" id="heat">Heat: --</span>
      <span class="badge" id="fan">Fan: --</span>
      <span class="badge" id="inside">Inside: --</span>
      <span class="badge" id="outside">Outside: --</span>
      <span class="badge" id="mode">Mode: --</span>
      <span style="margin-left:auto"></span>
      <button class="btn" id="on">Fan ON</button>
      <button class="btn" id="off">Fan OFF</button>
      <button class="btn" id="boost">Boost 5m</button>
      <button class="btn" id="follow">Toggle Follow</button>
    </div>
  </div>

  <div class="card">
    <div class="toolbar" style="margin-bottom:8px">
      <span style="font-size:12px;color:#9fb3c8;">Window:</span>
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
async function getState(){ const r = await fetch('/state?_=' + new Date().getTime(), {cache:'no-store'}); return await r.json(); }
async function cmd(body){ await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }

async function refreshBadges(){
  const s = await getState();
  el('heat').textContent   = 'Heat: ' + (s.furnace_on? 'ON':'OFF');
  el('fan').textContent    = 'Fan: ' + (s.fan_on? 'ON':'OFF') + (s.boost_until?' (boost)':'');
  el('inside').textContent = 'Inside: ' + (s.indoor_f!=null ? s.indoor_f.toFixed(1)+'°F':'--');
  el('outside').textContent= 'Outside: ' + (s.outdoor_f!=null ? s.outdoor_f.toFixed(1)+'°F':'--');
  el('mode').textContent   = 'Mode: ' + (s.mode || '--');
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
      maintainAspectRatio:false, interaction:{mode:'index', intersect:false},
      scales:{ y:{min:0,max:100,title:{display:true,text:'Scale (°F) and Heat 0/100'}}}
    }
  });
}

async function loadData(){
  const r = await fetch('/data?_=' + new Date().getTime(), {cache:'no-store'});
  raw = await r.json();
  draw(sliceByHours(raw, hours));
}

// window buttons
document.querySelectorAll('.btn[data-h]').forEach(b=> b.onclick=()=>{hours=parseInt(b.dataset.h,10); draw(sliceByHours(raw,hours))});

// instant UI response after commands
el('on').onclick     = async()=>{ await cmd({"fan":"on"});          await refreshBadges(); };
el('off').onclick    = async()=>{ await cmd({"fan":"off"});         await refreshBadges(); };
el('boost').onclick  = async()=>{ await cmd({"boost_minutes":5});   await refreshBadges(); };
el('follow').onclick = async()=>{ const s=await getState(); await cmd({"follow_heat": !s.follow_heat}); await refreshBadges(); };

// run once now, then every 60s: refresh badges + reload /data (chart)
async function loop() {
  try {
    await refreshBadges();
    await loadData();
  } catch(e) {
    console.error(e);
  } finally {
    setTimeout(loop, 60000); // 1 minute
  }
}

(async()=>{ await loadData(); await refreshBadges(); loop(); })();
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
  <a class="btn" href="/">Back</a>
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
        return render_template_string(COSTS_HTML, days=days, rows=[], totals={"runtime_min":0,"gas_usd":0.0,"elec_usd":0.0,"total_usd":0.0})

    by_day = {}
    with open(CSV, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = row.get("timestamp_iso","")
            d = parse_date_local(ts)
            if not d: continue
            gas_c = fnum(row.get("gas_cost_cents")) or 0.0
            elec_c = fnum(row.get("elec_cost_cents")) or 0.0
            runtime_min = 1 if gas_c > 0 else 0

            entry = by_day.setdefault(d, {"runtime_min":0, "gas_c":0.0, "elec_c":0.0})
            entry["runtime_min"] += runtime_min
            entry["gas_c"] += gas_c
            entry["elec_c"] += elec_c

    dates_sorted = sorted(by_day.keys(), reverse=True)[:days]
    rows = []
    totals = {"runtime_min":0, "gas_usd":0.0, "elec_usd":0.0, "total_usd":0.0}
    for d in sorted(dates_sorted):
        e = by_day[d]
        gas_usd = e["gas_c"]/100.0
        elec_usd = e["elec_c"]/100.0
        total_usd = gas_usd + elec_usd
        rows.append({
            "date": d,
            "runtime_min": e["runtime_min"],
            "gas_usd": gas_usd,
            "elec_usd": elec_usd,
            "total_usd": total_usd
        })
        totals["runtime_min"] += e["runtime_min"]
        totals["gas_usd"] += gas_usd
        totals["elec_usd"] += elec_usd
        totals["total_usd"] += total_usd

    return render_template_string(COSTS_HTML, days=days, rows=rows, totals=totals)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001, debug=False)
