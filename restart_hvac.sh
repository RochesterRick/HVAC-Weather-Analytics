#!/bin/bash
# restart_hvac.sh
# Robust restart for HVAC daemons (meter.py + web.py), preferring systemd.
# - Stops services cleanly (prevents auto-restart thrash)
# - Ensures port 8001 is free before starting web
# - Starts via systemd if units exist; falls back to nohup
# - Verifies health with /state and /data

set -euo pipefail

APP_DIR="/home/pi/hvac"
WEB_PORT=8001
SVC_WEB="hvac-web.service"
SVC_METER="hvac-meter.service"

log(){ printf "[%s] %s\n" "$(date '+%H:%M:%S')" "$*"; }

have_cmd(){ command -v "$1" >/dev/null 2>&1; }

have_systemd(){
  have_cmd systemctl && systemctl list-unit-files | grep -q "^$SVC_WEB" && systemctl list-unit-files | grep -q "^$SVC_METER"
}

wait_port_free(){
  local port="$1" tries=20
  while ((tries-- > 0)); do
    if ! sudo ss -ltnp 2>/dev/null | grep -q ":$port "; then
      return 0
    fi
    sleep 0.3
  done
  return 1
}

wait_port_listen(){
  local port="$1" tries=50
  while ((tries-- > 0)); do
    if sudo ss -ltnp 2>/dev/null | grep -q ":$port "; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

health_check(){
  local url code
  url="http://127.0.0.1:${WEB_PORT}/state"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$url" || true)
  log "Health /state -> HTTP $code"
  url="http://127.0.0.1:${WEB_PORT}/data"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$url" || true)
  log "Health /data  -> HTTP $code"
}

log "Restarting HVAC daemons…"
cd "$APP_DIR" || { echo "Cannot cd to $APP_DIR"; exit 1; }

if have_systemd; then
  log "systemd units detected. Stopping services…"
  sudo systemctl stop "$SVC_WEB" "$SVC_METER" || true

  # Extra safety: kill stray processes (in case an old nohup is still around)
  log "Killing stray python processes (if any)…"
  pkill -f "python3.*web.py"   2>/dev/null || true
  pkill -f "python3.*meter.py" 2>/dev/null || true

  log "Ensuring port $WEB_PORT is free…"
  if ! wait_port_free "$WEB_PORT"; then
    log "Port $WEB_PORT still busy; force-killing listener…"
    # Find and nuke whatever holds the port
    if have_cmd fuser; then
      sudo fuser -k "${WEB_PORT}/tcp" || true
    else
      # Fall back to ss + awk + kill
      pids=$(sudo ss -ltnp | awk -v p=":$WEB_PORT" '$4 ~ p {print $7}' | tr ',' '\n' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)
      if [[ -n "${pids:-}" ]]; then
        sudo kill -9 $pids || true
      fi
    fi
  fi

  log "Starting services…"
  # If you changed unit files recently, uncomment the next two:
  # sudo systemctl daemon-reload
  # sudo systemctl daemon-reexec

  sudo systemctl start "$SVC_METER"
  sudo systemctl start "$SVC_WEB"

  log "Waiting for web to listen on :$WEB_PORT…"
  wait_port_listen "$WEB_PORT" || { log "Web did not bind to port $WEB_PORT"; exit 1; }

  log "Services status:"
  systemctl --no-pager --type=service | grep -E "hvac-(web|meter)\.service" || true

else
  log "systemd units NOT found. Using nohup mode…"

  log "Stopping old processes…"
  pkill -f "python3.*web.py"   2>/dev/null || true
  pkill -f "python3.*meter.py" 2>/dev/null || true
  sleep 1

  log "Ensuring port $WEB_PORT is free…"
  wait_port_free "$WEB_PORT" || {
    log "Port $WEB_PORT still busy; killing holder…"
    pids=$(sudo ss -ltnp | awk -v p=":$WEB_PORT" '$4 ~ p {print $7}' | tr ',' '\n' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)
    if [[ -n "${pids:-}" ]]; then
      sudo kill -9 $pids || true
    fi
  }

  log "Starting meter.py (nohup)…"
  nohup python3 "$APP_DIR/meter.py" > "$APP_DIR/meter.out" 2>&1 &
  sleep 0.5
  log "Starting web.py (nohup)…"
  nohup python3 "$APP_DIR/web.py"   > "$APP_DIR/web.out"   2>&1 &

  log "Waiting for web to listen on :$WEB_PORT…"
  wait_port_listen "$WEB_PORT" || { log "Web did not bind to port $WEB_PORT"; exit 1; }

  log "PIDs:"
  pgrep -af "python3 .*meter.py" || true
  pgrep -af "python3 .*web.py"   || true
fi

health_check
log "Done."
