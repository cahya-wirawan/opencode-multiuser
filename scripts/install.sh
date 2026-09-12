#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${BASE_DOMAIN+x}" ]]; then BASE_DOMAIN_EXPLICIT=1; fi
BASE_DOMAIN=${BASE_DOMAIN:-code.example.com}
OPENCODE_VERSION=${OPENCODE_VERSION:-1.18.30}
if [[ -n "${CONTROL_PLANE_PORT+x}" ]]; then CONTROL_PLANE_PORT_EXPLICIT=1; fi
CONTROL_PLANE_PORT=${CONTROL_PLANE_PORT:-8010}
CONTROL_PLANE_BIND=${CONTROL_PLANE_BIND:-0.0.0.0}
TRAEFIK_PUBLIC_PORT=${TRAEFIK_PUBLIC_PORT:-8443}
TRAEFIK_ENTRYPOINT=${TRAEFIK_ENTRYPOINT:-websecure}
WORKSPACE_HOST_PORT_BASE=${WORKSPACE_HOST_PORT_BASE:-41000}
SELF_DIR=$(cd "$(dirname "$0")/.." && pwd)

# v6.4+ requires the portal injection module. Refuse to perform a partial
# upgrade if the extracted source tree is incomplete.
for required_file in app/portal.py app/ui.py; do
  if [[ ! -f "$SELF_DIR/$required_file" ]]; then
    echo "ERROR: $SELF_DIR/$required_file is missing." >&2
    echo "Extract the complete release archive and run install.sh from that tree." >&2
    exit 1
  fi
done

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_CANDIDATES=("$PYTHON_BIN")
else
  PYTHON_CANDIDATES=(python3.13 python3.12 python3.11 python3)
fi

PYTHON_BIN=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v "$candidate")
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: Python 3.11 or newer is required." >&2
  echo "Install Python 3.11+ or rerun with PYTHON_BIN=/path/to/python3.11 ./scripts/install.sh" >&2
  exit 1
fi

echo "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"

USER_UID=$(id -u)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/${USER_UID}}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}

if [[ ! -d "$XDG_RUNTIME_DIR" || ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
  cat >&2 <<EOF
ERROR: the systemd user runtime/bus is not available for $USER (UID $USER_UID).
Expected:
  $XDG_RUNTIME_DIR
  $XDG_RUNTIME_DIR/bus

Run as root (or via sudo):
  loginctl enable-linger $USER
  systemctl start user-runtime-dir@${USER_UID}.service
  systemctl start user@${USER_UID}.service

Then log in again as $USER and rerun the installer.
Do not manually create /run/user/${USER_UID}.
EOF
  exit 1
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "ERROR: systemctl --user cannot reach the user manager via $DBUS_SESSION_BUS_ADDRESS" >&2
  exit 1
fi

CGROUP_CTRL="/sys/fs/cgroup/user.slice/user-${USER_UID}.slice/user@${USER_UID}.service/cgroup.controllers"
if [[ -r "$CGROUP_CTRL" ]]; then
  controllers=" $(cat "$CGROUP_CTRL") "
  missing=()
  for ctrl in cpu memory pids; do
    [[ "$controllers" == *" $ctrl "* ]] || missing+=("$ctrl")
  done
  if (( ${#missing[@]} )); then
    cat >&2 <<EOF
ERROR: missing delegated cgroup controllers for rootless Podman: ${missing[*]}
Current controllers: $(cat "$CGROUP_CTRL")

Configure the host as root, for example:
  mkdir -p /etc/systemd/system/user@.service.d
  cat > /etc/systemd/system/user@.service.d/delegate.conf <<'EOC'
[Service]
Delegate=cpu cpuset io memory pids
EOC
  systemctl daemon-reload
  systemctl restart user@${USER_UID}.service

Then log in again as $USER and rerun the installer.
EOF
    exit 1
  fi
else
  echo "WARNING: could not read $CGROUP_CTRL; CPU/memory/pids delegation could not be preflighted." >&2
fi

TARGET="$HOME/opt/opencode-multiuser"
CFG="$HOME/.config/opencode-multiuser"
QUADLET="$HOME/.config/containers/systemd"
USER_SYSTEMD="$HOME/.config/systemd/user"
DATA="$HOME/.local/share/opencode-multiuser"

mkdir -p "$TARGET" "$CFG" "$QUADLET" "$USER_SYSTEMD" "$DATA/traefik-dynamic" "$DATA/traefik"

# On upgrades, reuse persisted public host and port unless explicitly overridden.
if [[ -f "$CFG/control-plane.env" ]]; then
  if [[ -z "${BASE_DOMAIN_EXPLICIT:-}" ]]; then
    persisted_domain=$(sed -n 's/^BASE_DOMAIN=//p' "$CFG/control-plane.env" | tail -1)
    [[ -n "$persisted_domain" ]] && BASE_DOMAIN=$persisted_domain
  fi
  if [[ -z "${CONTROL_PLANE_PORT_EXPLICIT:-}" ]]; then
    persisted_port=$(sed -n 's/^CONTROL_PLANE_PORT=//p' "$CFG/control-plane.env" | tail -1)
    [[ -n "$persisted_port" ]] && CONTROL_PLANE_PORT=$persisted_port
  fi
fi

systemctl --user stop opencode-control-plane.service >/dev/null 2>&1 || true

if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :${CONTROL_PLANE_PORT}" 2>/dev/null | grep -q .; then
  cat >&2 <<EOF
ERROR: CONTROL_PLANE_PORT=${CONTROL_PLANE_PORT} is already in use.
Choose another free port, for example:
  CONTROL_PLANE_PORT=8011 PYTHON_BIN=${PYTHON_BIN} ./scripts/install.sh
EOF
  exit 1
fi

rsync -a --delete --exclude .venv --exclude .pytest_cache "$SELF_DIR/" "$TARGET/"

for required_file in app/portal.py app/ui.py; do
  if [[ ! -f "$TARGET/$required_file" ]]; then
    echo "ERROR: upgrade copy is incomplete: $TARGET/$required_file was not installed." >&2
    exit 1
  fi
done
cp "$TARGET/systemd/quadlet/"* "$QUADLET/"
cp "$TARGET/systemd/opencode-control-plane.service" "$USER_SYSTEMD/"
cp "$TARGET/traefik/traefik.yml" "$CFG/traefik.yml"

if [[ ! -f "$CFG/postgres.env" ]]; then
  POSTGRES_PASSWORD=$(openssl rand -hex 24)
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD" > "$CFG/postgres.env"
  chmod 600 "$CFG/postgres.env"
else
  POSTGRES_PASSWORD=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$CFG/postgres.env")
fi

if [[ ! -f "$CFG/control-plane.env" ]]; then
  JWT_SECRET=$(openssl rand -hex 32)
  cat > "$CFG/control-plane.env" <<ENV
BASE_DOMAIN=$BASE_DOMAIN
CONTROL_PLANE_BIND=$CONTROL_PLANE_BIND
CONTROL_PLANE_PORT=$CONTROL_PLANE_PORT
CONTROL_PLANE_URL=http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT
TRAEFIK_ENTRYPOINT=$TRAEFIK_ENTRYPOINT
DATABASE_URL=postgresql+psycopg://opencode:${POSTGRES_PASSWORD}@127.0.0.1:5432/opencode
JWT_SECRET=$JWT_SECRET
ALLOW_REGISTRATION=true
SESSION_COOKIE_NAME=oc_session
WORKSPACE_COOKIE_NAME=oc_workspace
COOKIE_SECURE=false
PODMAN_BIN=/usr/bin/podman
WORKSPACE_IMAGE=localhost/opencode-workspace:latest
PODMAN_NETWORK=opencode-net
DATA_ROOT=$DATA
MAX_SLOTS=10
WORKSPACE_MEMORY=8g
WORKSPACE_CPUS=4
WORKSPACE_PIDS_LIMIT=1024
WORKSPACE_READY_TIMEOUT_SECONDS=45
WORKSPACE_READY_POLL_INTERVAL_SECONDS=0.5
WORKSPACE_IDLE_TIMEOUT_MINUTES=30
WORKSPACE_REAPER_INTERVAL_SECONDS=60
STOP_WORKSPACES_ON_LOGOUT=true
WORKSPACE_HOST_PORT_BASE=$WORKSPACE_HOST_PORT_BASE
TRAEFIK_DYNAMIC_DIR=$DATA/traefik-dynamic
TRAEFIK_TLS=false
TRAEFIK_CERT_RESOLVER=letsencrypt
ENV
  chmod 600 "$CFG/control-plane.env"
else
  # Preserve existing values and append v6 keys if they are absent.
  grep -q '^CONTROL_PLANE_BIND=' "$CFG/control-plane.env" || echo "CONTROL_PLANE_BIND=$CONTROL_PLANE_BIND" >> "$CFG/control-plane.env"
  grep -q '^CONTROL_PLANE_PORT=' "$CFG/control-plane.env" || echo "CONTROL_PLANE_PORT=$CONTROL_PLANE_PORT" >> "$CFG/control-plane.env"
  grep -q '^TRAEFIK_ENTRYPOINT=' "$CFG/control-plane.env" || echo "TRAEFIK_ENTRYPOINT=$TRAEFIK_ENTRYPOINT" >> "$CFG/control-plane.env"
  grep -q '^SESSION_COOKIE_NAME=' "$CFG/control-plane.env" || echo "SESSION_COOKIE_NAME=oc_session" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_COOKIE_NAME=' "$CFG/control-plane.env" || echo "WORKSPACE_COOKIE_NAME=oc_workspace" >> "$CFG/control-plane.env"
  grep -q '^COOKIE_SECURE=' "$CFG/control-plane.env" || echo "COOKIE_SECURE=false" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_HOST_PORT_BASE=' "$CFG/control-plane.env" || echo "WORKSPACE_HOST_PORT_BASE=$WORKSPACE_HOST_PORT_BASE" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_READY_TIMEOUT_SECONDS=' "$CFG/control-plane.env" || echo "WORKSPACE_READY_TIMEOUT_SECONDS=45" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_READY_POLL_INTERVAL_SECONDS=' "$CFG/control-plane.env" || echo "WORKSPACE_READY_POLL_INTERVAL_SECONDS=0.5" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_IDLE_TIMEOUT_MINUTES=' "$CFG/control-plane.env" || echo "WORKSPACE_IDLE_TIMEOUT_MINUTES=30" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_REAPER_INTERVAL_SECONDS=' "$CFG/control-plane.env" || echo "WORKSPACE_REAPER_INTERVAL_SECONDS=60" >> "$CFG/control-plane.env"
  grep -q '^STOP_WORKSPACES_ON_LOGOUT=' "$CFG/control-plane.env" || echo "STOP_WORKSPACES_ON_LOGOUT=true" >> "$CFG/control-plane.env"

  # v6 uses one public gateway URL. Rewrite legacy public URL defaults while
  # preserving a custom value if the administrator already set one.
  if grep -q '^CONTROL_PLANE_URL=http://code.example.com' "$CFG/control-plane.env"; then
    sed -i "s|^CONTROL_PLANE_URL=.*|CONTROL_PLANE_URL=http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT|" "$CFG/control-plane.env"
  fi

  # Older releases may have persisted an HTTPS public URL even while Traefik
  # was deliberately running without TLS. Normalize that known upgrade case so
  # browser cookies and /open/<workspace> redirects remain on working HTTP.
  EFFECTIVE_TRAEFIK_TLS=$(sed -n 's/^TRAEFIK_TLS=//p' "$CFG/control-plane.env" | tail -1 | tr '[:upper:]' '[:lower:]')
  CURRENT_PUBLIC_URL=$(sed -n 's/^CONTROL_PLANE_URL=//p' "$CFG/control-plane.env" | tail -1)
  if [[ "${EFFECTIVE_TRAEFIK_TLS:-false}" != "true" && "$CURRENT_PUBLIC_URL" == https://$BASE_DOMAIN* ]]; then
    sed -i "s|^CONTROL_PLANE_URL=.*|CONTROL_PLANE_URL=http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT|" "$CFG/control-plane.env"
  fi
fi

EFFECTIVE_CONTROL_PLANE_PORT=$(sed -n 's/^CONTROL_PLANE_PORT=//p' "$CFG/control-plane.env" | tail -1)
EFFECTIVE_CONTROL_PLANE_PORT=${EFFECTIVE_CONTROL_PLANE_PORT:-$CONTROL_PLANE_PORT}
EFFECTIVE_TRAEFIK_ENTRYPOINT=$(sed -n 's/^TRAEFIK_ENTRYPOINT=//p' "$CFG/control-plane.env" | tail -1)
EFFECTIVE_TRAEFIK_ENTRYPOINT=${EFFECTIVE_TRAEFIK_ENTRYPOINT:-$TRAEFIK_ENTRYPOINT}

sed \
  -e "s|__BASE_DOMAIN__|$BASE_DOMAIN|g" \
  -e "s|__CONTROL_PLANE_PORT__|$EFFECTIVE_CONTROL_PLANE_PORT|g" \
  -e "s|__TRAEFIK_ENTRYPOINT__|$EFFECTIVE_TRAEFIK_ENTRYPOINT|g" \
  "$TARGET/traefik/dynamic/control-plane.yml" > "$DATA/traefik-dynamic/control-plane.yml"

# Remove v5 per-workspace hostname routes. v6 proxies every workspace through
# the single control-plane/gateway route.
rm -f "$DATA"/traefik-dynamic/workspace-*.yml

rm -rf "$TARGET/.venv"
"$PYTHON_BIN" -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/python" -m pip install --upgrade pip
"$TARGET/.venv/bin/python" -m pip install -e "$TARGET"

podman build --build-arg "OPENCODE_VERSION=$OPENCODE_VERSION" -t localhost/opencode-workspace:latest -f "$TARGET/Containerfile.workspace" "$TARGET"
podman pull docker.io/library/postgres:17
podman pull docker.io/library/traefik:v3.5

systemctl --user daemon-reload
systemctl --user start opencode-network.service postgres.service traefik.service
sleep 2
systemctl --user enable opencode-control-plane.service
systemctl --user restart opencode-control-plane.service

cat <<MSG
Installed OpenCode Multiuser v6.5 (single-host gateway + redesigned portal UI).

Public gateway:
  http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT

Control plane direct port:
  ${CONTROL_PLANE_BIND}:${EFFECTIVE_CONTROL_PLANE_PORT}
  (keep this port blocked from untrusted networks; Traefik is the public entry point)

Dashboard:
  http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT/dashboard

Persistent config:
  $CFG/control-plane.env

Capacity:
  MAX_SLOTS controls simultaneous workspace containers.
  Workspace containers are created on demand; zero are prestarted.
  Each slot publishes OpenCode only on 127.0.0.1 at
  WORKSPACE_HOST_PORT_BASE + slot number.

DNS:
  Only $BASE_DOMAIN -> this host is required.
  Wildcard workspace DNS is no longer used.

Upgrade note:
  Existing v5 workspace containers are recycled automatically the next time
  /workspaces/start is called for that project so they acquire the gateway
  secret and loopback port mapping.
MSG
