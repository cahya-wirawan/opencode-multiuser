#!/usr/bin/env bash
set -euo pipefail

BASE_DOMAIN=${BASE_DOMAIN:-code.example.com}
OPENCODE_VERSION=${OPENCODE_VERSION:-1.18.30}
SELF_DIR=$(cd "$(dirname "$0")/.." && pwd)
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

python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install --upgrade pip
"$TARGET/.venv/bin/pip" install -e "$TARGET"

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
