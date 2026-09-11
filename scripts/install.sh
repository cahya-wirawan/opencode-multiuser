#!/usr/bin/env bash
set -euo pipefail

BASE_DOMAIN=${BASE_DOMAIN:-code.example.com}
OPENCODE_VERSION=${OPENCODE_VERSION:-1.18.30}
if [[ -n "${CONTROL_PLANE_PORT+x}" ]]; then CONTROL_PLANE_PORT_EXPLICIT=1; fi
CONTROL_PLANE_PORT=${CONTROL_PLANE_PORT:-8010}
TRAEFIK_PUBLIC_PORT=${TRAEFIK_PUBLIC_PORT:-8443}
TRAEFIK_ENTRYPOINT=${TRAEFIK_ENTRYPOINT:-websecure}
SELF_DIR=$(cd "$(dirname "$0")/.." && pwd)

# The control plane requires Python >= 3.11. RHEL 9 commonly exposes 3.9
# as `python3`, so prefer a newer explicitly-versioned interpreter when present.
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

# Rootless Podman and systemctl --user require the systemd user runtime/bus.
USER_UID=$(id -u)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/${USER_UID}}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}

if [[ ! -d "$XDG_RUNTIME_DIR" || ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
  cat >&2 <<EOF
ERROR: the systemd user runtime/bus is not available for $USER (UID $USER_UID).
Expected:
  $XDG_RUNTIME_DIR
  $XDG_RUNTIME_DIR/bus

Run as root (or via sudo) before installing:
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

# Resource-limited rootless containers need cgroup v2 controllers delegated to
# the user manager. Fail early instead of producing a cryptic crun error later.
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

# On upgrades, honor the already-persisted control-plane port unless the user
# explicitly supplied CONTROL_PLANE_PORT for this run.
if [[ -f "$CFG/control-plane.env" && -z "${CONTROL_PLANE_PORT_EXPLICIT:-}" ]]; then
  persisted_port=$(sed -n 's/^CONTROL_PLANE_PORT=//p' "$CFG/control-plane.env" | tail -1)
  [[ -n "$persisted_port" ]] && CONTROL_PLANE_PORT=$persisted_port
fi

# Stop the existing control plane before replacing files and checking its port.
systemctl --user stop opencode-control-plane.service >/dev/null 2>&1 || true

# If the selected local Uvicorn port is occupied by another process, stop here
# with a useful error. Override with CONTROL_PLANE_PORT=<free-port>.
if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :${CONTROL_PLANE_PORT}" 2>/dev/null | grep -q .; then
  cat >&2 <<EOF
ERROR: CONTROL_PLANE_PORT=${CONTROL_PLANE_PORT} is already in use.
Choose another free loopback port, for example:
  CONTROL_PLANE_PORT=8011 PYTHON_BIN=${PYTHON_BIN} ./scripts/install.sh
EOF
  exit 1
fi

rsync -a --delete --exclude .venv "$SELF_DIR/" "$TARGET/"
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
CONTROL_PLANE_PORT=$CONTROL_PLANE_PORT
CONTROL_PLANE_URL=http://$BASE_DOMAIN:$TRAEFIK_PUBLIC_PORT
WORKSPACE_SCHEME=http
WORKSPACE_PUBLIC_PORT=$TRAEFIK_PUBLIC_PORT
TRAEFIK_ENTRYPOINT=$TRAEFIK_ENTRYPOINT
DATABASE_URL=postgresql+psycopg://opencode:${POSTGRES_PASSWORD}@127.0.0.1:5432/opencode
JWT_SECRET=$JWT_SECRET
ALLOW_REGISTRATION=true
PODMAN_BIN=/usr/bin/podman
WORKSPACE_IMAGE=localhost/opencode-workspace:latest
PODMAN_NETWORK=opencode-net
DATA_ROOT=$DATA
MAX_SLOTS=10
WORKSPACE_MEMORY=8g
WORKSPACE_CPUS=4
WORKSPACE_PIDS_LIMIT=1024
TRAEFIK_DYNAMIC_DIR=$DATA/traefik-dynamic
TRAEFIK_TLS=false
TRAEFIK_CERT_RESOLVER=letsencrypt
ENV
  chmod 600 "$CFG/control-plane.env"
else
  # Preserve existing values, but append keys introduced after v4 when missing.
  grep -q '^CONTROL_PLANE_PORT=' "$CFG/control-plane.env" || echo "CONTROL_PLANE_PORT=$CONTROL_PLANE_PORT" >> "$CFG/control-plane.env"
  grep -q '^WORKSPACE_PUBLIC_PORT=' "$CFG/control-plane.env" || echo "WORKSPACE_PUBLIC_PORT=$TRAEFIK_PUBLIC_PORT" >> "$CFG/control-plane.env"
  grep -q '^TRAEFIK_ENTRYPOINT=' "$CFG/control-plane.env" || echo "TRAEFIK_ENTRYPOINT=$TRAEFIK_ENTRYPOINT" >> "$CFG/control-plane.env"
fi

# Use the effective values from the persistent environment file when upgrading.
EFFECTIVE_CONTROL_PLANE_PORT=$(sed -n 's/^CONTROL_PLANE_PORT=//p' "$CFG/control-plane.env" | tail -1)
EFFECTIVE_CONTROL_PLANE_PORT=${EFFECTIVE_CONTROL_PLANE_PORT:-$CONTROL_PLANE_PORT}
EFFECTIVE_TRAEFIK_ENTRYPOINT=$(sed -n 's/^TRAEFIK_ENTRYPOINT=//p' "$CFG/control-plane.env" | tail -1)
EFFECTIVE_TRAEFIK_ENTRYPOINT=${EFFECTIVE_TRAEFIK_ENTRYPOINT:-$TRAEFIK_ENTRYPOINT}

sed \
  -e "s|__BASE_DOMAIN__|$BASE_DOMAIN|g" \
  -e "s|__CONTROL_PLANE_PORT__|$EFFECTIVE_CONTROL_PLANE_PORT|g" \
  -e "s|__TRAEFIK_ENTRYPOINT__|$EFFECTIVE_TRAEFIK_ENTRYPOINT|g" \
  "$TARGET/traefik/dynamic/control-plane.yml" > "$DATA/traefik-dynamic/control-plane.yml"

# Always recreate the venv so a previous failed install made with an older
# interpreter cannot be accidentally reused.
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
systemctl --user enable --now opencode-control-plane.service

cat <<MSG
Installed.

Control plane (direct): http://127.0.0.1:${EFFECTIVE_CONTROL_PLANE_PORT}
Traefik HTTP listener: http://127.0.0.1:8080
Traefik :8443 listener is also HTTP until TRAEFIK_TLS=true is configured.

Persistent config:
  $CFG/control-plane.env

Capacity:
  MAX_SLOTS controls the maximum simultaneous OpenCode workspace containers.
  Workspace containers are created on demand; zero are prestarted.

DNS:
  $BASE_DOMAIN -> this host
  *.$BASE_DOMAIN -> this host
MSG
