#!/usr/bin/env bash
set -euo pipefail

BASE_DOMAIN=${BASE_DOMAIN:-code.example.com}
OPENCODE_VERSION=${OPENCODE_VERSION:-1.18.30}
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
  echo "Your default python3 may be older (RHEL 9 commonly uses Python 3.9)." >&2
  echo "Install Python 3.11+ or rerun with PYTHON_BIN=/path/to/python3.11 ./scripts/install.sh" >&2
  exit 1
fi

echo "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"
TARGET="$HOME/opt/opencode-multiuser"
CFG="$HOME/.config/opencode-multiuser"
QUADLET="$HOME/.config/containers/systemd"
USER_SYSTEMD="$HOME/.config/systemd/user"
DATA="$HOME/.local/share/opencode-multiuser"

mkdir -p "$TARGET" "$CFG" "$QUADLET" "$USER_SYSTEMD" "$DATA/traefik-dynamic" "$DATA/traefik" "$DATA/postgres"
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
CONTROL_PLANE_URL=https://$BASE_DOMAIN
WORKSPACE_SCHEME=https
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
fi

sed "s/code.example.com/$BASE_DOMAIN/g" "$TARGET/traefik/dynamic/control-plane.yml" > "$DATA/traefik-dynamic/control-plane.yml"

# Always recreate the venv so a previous failed install made with an older
# interpreter (for example Python 3.9) cannot be accidentally reused.
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

Control plane: http://127.0.0.1:8000
Traefik HTTP:  http://127.0.0.1:8080
Traefik HTTPS: https://127.0.0.1:8443 (TLS must still be configured)

For services to survive logout, run as root once:
  loginctl enable-linger $USER

Also configure DNS:
  $BASE_DOMAIN -> this host
  *.$BASE_DOMAIN -> this host
MSG
