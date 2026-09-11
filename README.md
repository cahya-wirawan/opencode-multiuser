# OpenCode Multiuser v6 — single-host gateway

A multi-user OpenCode control plane for **rootless Podman + systemd/Quadlet + Traefik**. v6 removes per-workspace public hostnames and routes every user through one public URL.

## Architecture

```text
Browser / API client
        |
        | http://code.example.com:8443
        v
     Traefik
        |
        v
FastAPI control plane + authenticated gateway
        |
        | validate JWT/session + selected workspace
        | inject OpenCode Basic Auth server-side
        |
        +--> 127.0.0.1:41001 --> slot 1 container :4096
        +--> 127.0.0.1:41002 --> slot 2 container :4096
        +--> 127.0.0.1:41003 --> slot 3 container :4096
                     ...
```

Each workspace container is still isolated and disposable. Its repository, OpenCode data/state/config, and gateway runtime secret live outside the container under the owning user's persistent workspace directory.

## What changed from v5

- No `w-<workspace>.BASE_DOMAIN` hostnames.
- No wildcard DNS requirement.
- No per-workspace Traefik dynamic router files.
- Traefik has one router for `BASE_DOMAIN` and forwards all requests to the FastAPI gateway.
- A workspace container publishes port 4096 **only on host loopback** using a slot port: `WORKSPACE_HOST_PORT_BASE + slot_id`.
- The browser uses an HttpOnly `oc_session` authentication cookie and `oc_workspace` selection cookie.
- `/open/<workspace-id>` validates ownership, selects the workspace, and redirects to `/`.
- API clients may select a workspace with `X-OpenCode-Workspace: <workspace-id-or-container-name>`; the gateway validates ownership before routing.
- The client routing header and gateway cookies are stripped before proxying to OpenCode.
- The gateway injects the container's OpenCode Basic Auth credential server-side.
- HTTP and WebSocket proxying are both supported.

## Requirements

- Linux with cgroup v2
- Podman 5.x+
- systemd user services / Quadlet
- Python 3.11+
- `openssl`, `rsync`
- delegated `cpu`, `memory`, and `pids` controllers for the rootless user
- one DNS A/AAAA record: `code.example.com -> host`

Check the host before installation:

```bash
./scripts/check-host.sh
```

Rootless user setup, as root:

```bash
loginctl enable-linger llm_apps
UID_LLM=$(id -u llm_apps)
systemctl start user-runtime-dir@${UID_LLM}.service
systemctl start user@${UID_LLM}.service
```

If cgroup controllers are not delegated, a typical host override is:

```ini
# /etc/systemd/system/user@.service.d/delegate.conf
[Service]
Delegate=cpu cpuset io memory pids
```

then:

```bash
systemctl daemon-reload
systemctl restart user@$(id -u llm_apps).service
```

Log in again as the rootless service user afterward.

## Install

```bash
unzip opencode-multiuser-fixed-v6.zip
cd opencode-multiuser-fixed-v6

export BASE_DOMAIN=code-test.example.org
PYTHON_BIN=python3.11 ./scripts/install.sh
```

By default:

```text
Traefik public HTTP:         :8080 and :8443
Gateway/Uvicorn:             :8010
Workspace loopback base:     41000
Slot 1 OpenCode backend:     127.0.0.1:41001
Slot 2 OpenCode backend:     127.0.0.1:41002
...
```

`8443` is plain HTTP until TLS is explicitly configured.

The gateway binds `CONTROL_PLANE_BIND=0.0.0.0` so the Traefik container can reach `host.containers.internal:8010`. Keep the direct control-plane port blocked from untrusted networks with the host firewall; the intended public entry point is Traefik.

## Persistent configuration

The installer writes:

```text
~/.config/opencode-multiuser/control-plane.env
~/.config/opencode-multiuser/postgres.env
```

Important v6 settings:

```bash
CONTROL_PLANE_BIND=0.0.0.0
CONTROL_PLANE_PORT=8010
CONTROL_PLANE_URL=http://code-test.example.org:8443
MAX_SLOTS=10
WORKSPACE_HOST_PORT_BASE=41000
SESSION_COOKIE_NAME=oc_session
WORKSPACE_COOKIE_NAME=oc_workspace
COOKIE_SECURE=false
```

`MAX_SLOTS=10` means at most ten simultaneous workspace containers. It does **not** prestart ten containers.

## Browser test

Open:

```text
http://code-test.example.org:8443/login
```

For a fresh installation, create the first account through the API:

```bash
CP=http://127.0.0.1:8010
curl -sS -X POST "$CP/auth/register" \
  -H 'content-type: application/json' \
  -d '{"username":"cahya","password":"replace-with-a-long-password"}' | jq
```

Then sign in through `/login`. `/dashboard` lets you start, open, and stop workspaces. Clicking **Open** sets the selected workspace cookie and sends the browser through the gateway to OpenCode.

## API test

Login and obtain a bearer token:

```bash
TOKEN=$(
  curl -sS -X POST "$CP/auth/login" \
    -H 'content-type: application/json' \
    -d '{"username":"cahya","password":"replace-with-a-long-password"}' |
  jq -r .access_token
)
```

Start a workspace:

```bash
curl -sS -X POST "$CP/workspaces/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"project_slug":"rag-project"}' | jq
```

The response now contains a URL such as:

```text
http://code-test.example.org:8443/open/<workspace-id>
```

For an API request directly through the gateway, select the workspace with a validated header:

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-OpenCode-Workspace: <workspace-id-or-container-name>" \
  http://code-test.example.org:8443/global/health | jq
```

The gateway verifies that the workspace is running and belongs to the authenticated user. It does not trust the header as an unrestricted container destination.

## Workspace storage

```text
~/.local/share/opencode-multiuser/users/<user-id>/<project>/
├── workspace/
├── opencode-data/
├── opencode-state/
├── opencode-config/
└── runtime-secret       # mode 0600; OpenCode Basic Auth secret
```

The OpenCode cache remains ephemeral in tmpfs.

## Upgrade from v5

v6 preserves PostgreSQL and the existing user/project storage. The installer removes old `workspace-*.yml` Traefik route files.

Existing v5 workspace containers lack the v6 loopback port mapping and `runtime-secret`. They appear in the dashboard with an **Upgrade workspace** action instead of **Open**. That action calls `/workspaces/start`, which automatically recycles the old container and recreates it in v6 gateway mode.

After upgrading, only this DNS record is required:

```text
code-test.example.org -> host
```

The old wildcard `*.code-test.example.org` record may remain, but v6 does not use it.

## Security notes

- The browser never receives the OpenCode Basic Auth password.
- Workspace backend ports bind only to `127.0.0.1`.
- Workspace selection is authorization-checked against the logged-in user.
- Client `Authorization` is replaced with OpenCode Basic Auth before forwarding.
- Gateway session/workspace cookies are stripped before forwarding to OpenCode.
- Containers remain read-only except for explicit persistent mounts and tmpfs paths.
- CPU, memory, PID, capability, and user-namespace restrictions remain enabled.
- Keep port `8010` blocked externally because it bypasses Traefik as an ingress layer.
- Enable HTTPS before sending credentials/source code over an untrusted network.

## Useful checks

```bash
systemctl --user status postgres.service traefik.service opencode-control-plane.service
podman ps
curl http://127.0.0.1:8010/healthz
ss -ltn | grep -E ':(8010|8080|8443|4100[0-9])\\b'
```

Expected health response:

```json
{"ok":true,"mode":"single-host-gateway"}
```


### HTTP cookie note

When Traefik is serving plain HTTP (`TRAEFIK_TLS=false`), keep `COOKIE_SECURE=false`. Set it to `true` only after the public gateway is actually HTTPS. This setting is intentionally independent from `CONTROL_PLANE_URL` so upgrades from older configurations do not break browser login cookies.
