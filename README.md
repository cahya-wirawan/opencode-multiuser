# OpenCode Multiuser

**Current version: 6.9.0**

OpenCode Multiuser turns OpenCode into a centrally managed, multi-user developer platform for enterprise and internal engineering environments. It provides authenticated access to isolated, disposable OpenCode workspaces while keeping project files and OpenCode state persistent across container restarts.

Instead of giving every developer a permanently running VM or asking users to manage OpenCode locally, the platform allocates an isolated rootless Podman container only when a workspace is needed. A central FastAPI control plane handles authentication, authorization, workspace lifecycle, routing, capacity limits, idle cleanup, and administrative controls. Traefik exposes a single portal URL, while OpenCode itself remains largely unmodified upstream software.

## Why this is useful for enterprises

OpenCode is powerful because it can inspect repositories, edit files, run commands, and invoke development tools. Those same capabilities make a shared deployment difficult to operate safely without isolation, identity, lifecycle management, and governance. OpenCode Multiuser adds that missing enterprise layer.

The platform is useful when an organization wants to provide AI-assisted development centrally while retaining control over infrastructure, source code, authentication, capacity, and runtime isolation. Typical benefits include:

- **Centralized access** — users sign in through one internal portal instead of installing and configuring OpenCode individually.
- **Strong workspace isolation** — every active workspace runs in its own rootless Podman container with CPU, memory, PID, capability, and filesystem restrictions.
- **Efficient infrastructure use** — containers are created on demand and destroyed when users stop, log out, or remain idle, so capacity is not permanently reserved per user.
- **Persistent developer state** — repositories, OpenCode data, state, and configuration survive container recreation while the runtime itself remains disposable.
- **Enterprise identity integration** — generic OpenID Connect supports Entra ID, Keycloak, Authentik, Okta, and other standards-compliant providers, while local accounts remain available as an optional fallback.
- **Role-based administration** — administrators can manage users, roles, account status, password resets, and active workspaces from the portal.
- **Single ingress point** — users access one portal hostname; workspace containers are not directly exposed to the network.
- **Controlled resource usage** — a configurable slot pool limits the number of simultaneously running workspaces.
- **Operational clarity** — readiness checks, loading states, stale-runtime reconciliation, idle reclamation, health endpoints, and systemd integration make the platform suitable for long-running internal operation.
- **On-premises friendly** — the system is designed for Linux, rootless Podman, systemd/Quadlet, PostgreSQL, and internal networks without requiring Kubernetes.

The result is closer to an internal developer platform than a simple OpenCode wrapper: users get an easy browser experience, while infrastructure teams retain control of runtime boundaries and capacity.

## Key capabilities

- Single-host web portal for login, registration, dashboard, administration, and OpenCode access
- Generic OIDC authentication with PKCE and optional local username/password fallback
- First-account administrator bootstrap and `admin` / `developer` roles
- Admin user-management console
- Admin system console with controlled OpenCode engine updates
- Rootless Podman workspace containers
- systemd/Quadlet-managed PostgreSQL and Traefik services
- One isolated runtime per active workspace
- Persistent per-user/per-project workspace and OpenCode state
- Configurable maximum concurrent workspace slots
- Automatic stop-on-logout
- Automatic idle-workspace reclamation
- OpenCode readiness checks before opening a workspace
- Stale-runtime reconciliation after crashes or host restarts
- Server-side injection of OpenCode Basic Auth credentials
- HTTP and WebSocket gateway proxying
- Management widget injected into OpenCode with Dashboard, Stop, and Logout actions
- Responsive enterprise portal UI with loading, error, toast, and confirmation states
- Semantic Versioning with reproducible ZIP release packaging

## Architecture

```text
                         Enterprise user
                               |
                               | HTTP/HTTPS
                               v
                    +----------------------+ 
                    |       Traefik        |
                    |  single public host  |
                    +----------+-----------+
                               |
                               v
              +----------------------------------+
              | FastAPI control plane / gateway |
              |                                  |
              | - authentication                 |
              | - authorization                  |
              | - user/admin management          |
              | - workspace scheduler            |
              | - readiness / idle cleanup       |
              | - HTTP + WebSocket proxy         |
              +-------------+--------------------+
                            |
                            | loopback-only backend ports
            +---------------+------------------+
            |               |                  |
            v               v                  v
      127.0.0.1:41001 127.0.0.1:41002   127.0.0.1:41003
            |               |                  |
            v               v                  v
      +-----------+    +-----------+      +-----------+
      | OpenCode  |    | OpenCode  |      | OpenCode  |
      | workspace |    | workspace |      | workspace |
      | container |    | container |      | container |
      +-----------+    +-----------+      +-----------+

              PostgreSQL stores users, slots,
              workspace leases and metadata.
```

The browser never connects directly to a workspace container. The gateway validates the user's selected workspace, checks ownership, injects the container's private OpenCode Basic Auth credential, and proxies HTTP/WebSocket traffic to the slot's loopback-only backend port.

## Workspace lifecycle

```text
User starts project
      |
      v
Allocate free slot
      |
      v
Create fresh rootless Podman container
      |
      v
Mount persistent user/project directories
      |
      v
Wait for authenticated OpenCode /global/health
      |
      v
Mark workspace RUNNING
      |
      v
Proxy browser traffic through gateway
      |
      +------ user stops / logs out / becomes idle ------+
                                                        |
                                                        v
                                             Destroy runtime container
                                                        |
                                                        v
                                                Release slot
                                                        |
                                                        v
                                         Keep persistent project state
```

`MAX_SLOTS=10` means **up to ten simultaneous workspace containers**. It does not prestart ten containers.

## Security model

The project is designed around disposable runtimes and explicit trust boundaries:

- Workspace containers run rootless under the service account.
- Workspace root filesystems are read-only except for explicit persistent mounts and tmpfs paths.
- Linux capabilities are dropped and `no-new-privileges` is enabled.
- CPU, memory, and PID limits are enforced through cgroup v2.
- User namespaces isolate container identities from the host.
- Workspace backend ports bind only to `127.0.0.1`.
- OpenCode Basic Auth passwords are generated per runtime and never exposed to the browser.
- The gateway validates workspace ownership before proxying traffic.
- Client authentication headers and portal cookies are stripped before requests are forwarded to OpenCode.
- Traefik does not receive the Podman socket.
- The control plane is the only component allowed to create and destroy workspace runtimes.
- OIDC access and refresh tokens are not persisted by the portal.
- Administrative actions are protected by backend role checks, not only hidden UI elements.

For production use, enable HTTPS at Traefik and restrict direct access to the control-plane port with the host firewall.

## Requirements

- Linux with cgroup v2
- Podman 5.x+
- systemd user services / Quadlet
- Python 3.11+
- PostgreSQL container support through Podman
- `openssl`
- `rsync`
- delegated `cpu`, `memory`, and `pids` cgroup controllers for the rootless service user
- one DNS A/AAAA record for the portal hostname

Run the supplied host preflight before installation:

```bash
./scripts/check-host.sh
```

### Rootless service-user setup

As root:

```bash
loginctl enable-linger llm_apps
UID_LLM=$(id -u llm_apps)
systemctl start user-runtime-dir@${UID_LLM}.service
systemctl start user@${UID_LLM}.service
```

If the required cgroup controllers are not delegated, a typical systemd override is:

```ini
# /etc/systemd/system/user@.service.d/delegate.conf
[Service]
Delegate=cpu cpuset io memory pids
```

Then:

```bash
systemctl daemon-reload
systemctl restart user@$(id -u llm_apps).service
```

Log in again as the rootless service user afterward.

## Installation

```bash
unzip opencode-multiuser-6.9.0.zip
cd opencode-multiuser-6.9.0

export BASE_DOMAIN=code-test.example.org
PYTHON_BIN=python3.11 ./scripts/install.sh
```

The installer configures:

```text
~/.config/containers/systemd/
  opencode.network
  postgres.container
  postgres-data.volume
  traefik.container

~/.config/systemd/user/
  opencode-control-plane.service

~/.config/opencode-multiuser/
  control-plane.env
  postgres.env
  traefik.yml

~/.local/share/opencode-multiuser/
  users/
  traefik-dynamic/
```

Default listeners are:

```text
Traefik public HTTP:         :8080 and :8443
Control plane / gateway:     :8010
Workspace loopback base:     41000
Slot 1 OpenCode backend:     127.0.0.1:41001
Slot 2 OpenCode backend:     127.0.0.1:41002
...
```

Port `8443` is plain HTTP until TLS is explicitly configured.

The gateway normally binds `CONTROL_PLANE_BIND=0.0.0.0` so the Traefik container can reach `host.containers.internal:<CONTROL_PLANE_PORT>`. Restrict that port with the host firewall; Traefik should be the intended public ingress.

## Configuration

The primary runtime configuration is:

```text
~/.config/opencode-multiuser/control-plane.env
```

Common settings:

```bash
CONTROL_PLANE_BIND=0.0.0.0
CONTROL_PLANE_PORT=8010
CONTROL_PLANE_URL=http://code-test.example.org:8443

MAX_SLOTS=10
WORKSPACE_HOST_PORT_BASE=41000
WORKSPACE_READY_TIMEOUT_SECONDS=45
WORKSPACE_READY_POLL_INTERVAL_SECONDS=0.5
WORKSPACE_IDLE_TIMEOUT_MINUTES=30
WORKSPACE_REAPER_INTERVAL_SECONDS=60
STOP_WORKSPACES_ON_LOGOUT=true

WORKSPACE_IMAGE=localhost/opencode-workspace:latest
OPENCODE_VERSION=1.18.30
OPENCODE_REGISTRY_URL=https://registry.npmjs.org/opencode-ai/latest
OPENCODE_UPDATE_TIMEOUT_SECONDS=900

SESSION_COOKIE_NAME=oc_session
WORKSPACE_COOKIE_NAME=oc_workspace
COOKIE_SECURE=false

LOCAL_AUTH_ENABLED=true
ALLOW_REGISTRATION=true

OIDC_ENABLED=false
OIDC_ISSUER=
OIDC_DISCOVERY_URL=
OIDC_CLIENT_ID=
OIDC_CLIENT_SECRET=
OIDC_SCOPES=openid profile email
OIDC_DISPLAY_NAME=Corporate SSO
OIDC_REDIRECT_URI=
OIDC_USE_PKCE=true
OIDC_AUTO_PROVISION=true
OIDC_DEFAULT_ROLE=developer
```

When serving the portal over plain HTTP, keep `COOKIE_SECURE=false`. Set it to `true` only after the public endpoint is genuinely HTTPS.

## Authentication

### Local accounts

Local authentication is enabled by default.

Registration:

```text
http://code-test.example.org:8443/register
```

Login:

```text
http://code-test.example.org:8443/login
```

The first account provisioned in an empty database receives the **Admin** role. Later users default to **Developer**.

For an internal deployment, create the intended bootstrap administrator first and then consider disabling open registration:

```bash
ALLOW_REGISTRATION=false
```

### Generic OIDC

The portal supports standards-compliant OpenID Connect providers such as Microsoft Entra ID, Keycloak, Authentik, Okta, and others.

Register the portal as an OIDC web client and configure a callback similar to:

```text
http://code-test.example.org:8443/auth/oidc/callback
```

Then configure:

```bash
OIDC_ENABLED=true
OIDC_ISSUER=https://id.example.org/realms/developers
OIDC_CLIENT_ID=opencode-portal
OIDC_CLIENT_SECRET=replace-me
OIDC_SCOPES=openid profile email
OIDC_DISPLAY_NAME=Company SSO
OIDC_USE_PKCE=true
OIDC_AUTO_PROVISION=true
OIDC_DEFAULT_ROLE=developer
```

`OIDC_DISCOVERY_URL` normally stays empty because the portal derives the provider metadata URL from `OIDC_ISSUER`. `OIDC_REDIRECT_URI` also normally stays empty and is derived from `CONTROL_PLANE_URL`.

After configuration:

```bash
systemctl --user restart opencode-control-plane.service
```

Once OIDC has been verified from a separate browser session, an SSO-only deployment can use:

```bash
LOCAL_AUTH_ENABLED=false
ALLOW_REGISTRATION=false
```

Do not disable local authentication before confirming that OIDC works; otherwise a configuration error could lock out administrators.

OIDC identities use `(issuer, sub)` as the immutable identity key. Email, display name, and preferred username are profile attributes rather than identity keys. OIDC access and refresh tokens are not stored by the portal.

## Roles and administration

The portal currently defines two roles:

- **Admin** — user administration plus normal workspace access
- **Developer** — normal workspace access

Admins can open:

```text
/admin/users
/admin/system
```

`/admin/users` provides user and role management. `/admin/system` provides portal/OpenCode version visibility and the controlled OpenCode image update workflow described below.

The user-management console supports:

- searching and filtering users
- viewing Local versus OIDC authentication source
- viewing role, account status, created time, last login, and active workspaces
- promoting developers to admin
- demoting admins with safeguards
- enabling and disabling accounts
- stopping another user's active workspaces
- resetting local-account passwords
- deleting eligible local accounts

Safety rules prevent an administrator from disabling, demoting, or deleting themselves, and prevent removal of the last enabled administrator.

## Dashboard and workspace usage

After login, users are sent to:

```text
/dashboard
```

The dashboard shows project/workspace cards, runtime status, slot availability, and available actions.

Typical statuses include:

```text
STARTING
RUNNING
STOPPING
STOPPED
ERROR
```

Starting, stopping, and logout operations provide immediate loading/progress feedback so users can see that the action was accepted.

When a workspace is open, the gateway injects a small **Portal** control into the OpenCode page with:

- Dashboard
- Stop workspace
- Logout

Upstream OpenCode itself is not modified.

## Workspace readiness and recovery

A container reporting `Up` does not guarantee OpenCode is ready to accept requests. The control plane therefore polls the authenticated OpenCode health endpoint before marking a workspace `RUNNING`.

Defaults:

```bash
WORKSPACE_READY_TIMEOUT_SECONDS=45
WORKSPACE_READY_POLL_INTERVAL_SECONDS=0.5
```

If OpenCode never becomes healthy, the new runtime is removed and the slot is returned rather than exposing a transient broken workspace.

The control plane also reconciles workspace records against actual Podman runtimes at startup and during housekeeping. Missing containers or runtimes without their expected loopback mapping are marked stale, cleaned up, and their slots released.

## Automatic slot reclamation

Capacity is reclaimed in two ways.

### Logout cleanup

With:

```bash
STOP_WORKSPACES_ON_LOGOUT=true
```

logging out stops the user's active workspace containers and returns their slots immediately. Persistent project/OpenCode data is retained.

### Idle timeout

The gateway records workspace activity during HTTP requests, streamed responses, WebSocket activity, and workspace open/start operations. A background reaper stops workspaces that have remained inactive longer than:

```bash
WORKSPACE_IDLE_TIMEOUT_MINUTES=30
```

The reaper runs every:

```bash
WORKSPACE_REAPER_INTERVAL_SECONDS=60
```

Set the timeout to `0` to disable idle cleanup.

After an idle timeout, browser navigation returns the user to the dashboard with an explanation that the runtime was reclaimed. API traffic receives a machine-readable conflict response, and stale WebSockets are closed cleanly.

## Persistent workspace storage

Each user/project has persistent storage outside the disposable runtime container:

```text
~/.local/share/opencode-multiuser/users/<user-id>/<project>/
├── workspace/
├── opencode-data/
├── opencode-state/
├── opencode-config/
├── runtime-secret
├── last-activity
└── stop-reason
```

The OpenCode cache remains ephemeral in tmpfs.

Stopping or recreating a container does not delete the project workspace or OpenCode state.

## API usage

Local-auth API clients can obtain a bearer token:

```bash
CP=http://127.0.0.1:8010

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

The response includes a URL such as:

```text
http://code-test.example.org:8443/open/<workspace-id>
```

API clients can route through the single-host gateway with:

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-OpenCode-Workspace: <workspace-id-or-container-name>" \
  http://code-test.example.org:8443/global/health | jq
```

The gateway validates that the selected workspace is running and belongs to the authenticated user. The header is never treated as an unrestricted container destination.

## Operations and troubleshooting

Useful checks:

```bash
systemctl --user status \
  postgres.service \
  traefik.service \
  opencode-control-plane.service

podman ps
curl http://127.0.0.1:8010/healthz
curl http://127.0.0.1:8010/version
ss -ltn | grep -E ':(8010|8080|8443|4100[0-9])\\b'
```

A healthy response includes the application version and gateway mode.

Application logs:

```bash
journalctl --user -u opencode-control-plane.service -n 200 --no-pager
```

Workspace containers:

```bash
podman ps --filter 'name=oc-'
podman logs <workspace-container>
```

## Updating OpenCode itself

The **portal version** and the **OpenCode engine version** are independent. Upgrading OpenCode Multiuser does not require every OpenCode engine update to be a new portal release.

The configured OpenCode engine version is persisted in:

```text
~/.config/opencode-multiuser/control-plane.env
```

using:

```bash
OPENCODE_VERSION=1.18.30
```

### Recommended: Admin → System

Administrators can open:

```text
/admin/system
```

The System page shows:

- OpenCode Multiuser portal version
- configured OpenCode version
- version currently installed in `localhost/opencode-workspace:latest`
- upstream OpenCode version from the npm registry
- current image update/build state and log output

Use **Check latest** to query the configured registry and **Build & activate** to update to a chosen semantic version.

The update is deliberately transactional:

```text
request version
      |
      v
build localhost/opencode-workspace:<version>
      |
      v
run `opencode --version` smoke test
      |
      +---- failure ----> keep current `latest` image unchanged
      |
      v
retag tested candidate as localhost/opencode-workspace:latest
      |
      v
persist OPENCODE_VERSION in control-plane.env
```

The build runs in the background so the admin page remains responsive and can poll progress. Only administrators can start or inspect the update workflow.

**Running workspaces are not interrupted.** A container that is already running continues using the image it started with. Stop and start that workspace when you want it to use the newly activated OpenCode image.

### CLI/manual update

The same safe workflow can be performed manually. Example for `1.18.31`:

```bash
cd ~/opt/opencode-multiuser
TARGET_VERSION=1.18.31

podman build \
  --build-arg "OPENCODE_VERSION=${TARGET_VERSION}" \
  -t "localhost/opencode-workspace:${TARGET_VERSION}" \
  -f Containerfile.workspace .

podman run --rm \
  "localhost/opencode-workspace:${TARGET_VERSION}" \
  --version

podman tag \
  "localhost/opencode-workspace:${TARGET_VERSION}" \
  localhost/opencode-workspace:latest

sed -i \
  "s/^OPENCODE_VERSION=.*/OPENCODE_VERSION=${TARGET_VERSION}/" \
  ~/.config/opencode-multiuser/control-plane.env

systemctl --user restart opencode-control-plane.service
```

Alternatively, the installer honors an explicit version:

```bash
OPENCODE_VERSION=1.18.31 \
PYTHON_BIN=python3.11 \
./scripts/install.sh
```

On later upgrades, `install.sh` reuses the persisted `OPENCODE_VERSION` unless `OPENCODE_VERSION` is explicitly supplied in the shell.

## Versioning and releases

OpenCode Multiuser follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`). The single source of truth is:

```text
app/version.py
```

The Python package metadata, FastAPI/OpenAPI metadata, `/healthz`, `/version`, portal UI, and installer all read from this version.

Show or change the version:

```bash
python3 scripts/version.py show
python3 scripts/version.py bump patch   # 6.9.0 -> 6.9.1
python3 scripts/version.py bump minor   # 6.9.0 -> 6.10.0
python3 scripts/version.py bump major   # 6.9.0 -> 7.0.0
python3 scripts/version.py set 6.10.0
```

Build a release archive and checksum:

```bash
./scripts/package-release.sh
```

which creates:

```text
dist/opencode-multiuser-<version>.zip
dist/opencode-multiuser-<version>.zip.sha256
```

A typical release flow is:

```bash
python3 scripts/version.py bump patch
# update CHANGELOG.md
python3 -m pytest -q
./scripts/package-release.sh
```

See [`CHANGELOG.md`](CHANGELOG.md) for the complete release history and detailed evolution of the project.

## Production recommendations

Before exposing the portal beyond a controlled test network:

- enable HTTPS at Traefik
- set `COOKIE_SECURE=true`
- integrate with corporate OIDC/SSO
- disable open registration after bootstrap
- disable local authentication if organizational policy requires SSO-only access
- restrict direct access to the control-plane port
- review outbound network access for workspace containers
- use short-lived Git credentials or a credential broker rather than mounting host SSH keys
- monitor slot usage, memory, CPU, and workspace lifecycle events
- back up PostgreSQL and persistent workspace storage

## Release history

Detailed feature-by-feature changes have intentionally been moved out of this README. See [`CHANGELOG.md`](CHANGELOG.md) for the full history from the earliest prototype through version 6.9.0.
