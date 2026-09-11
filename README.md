# OpenCode Multiuser — rootless Podman + systemd + Traefik

A production-oriented MVP control plane for assigning isolated OpenCode runtime containers to authenticated users.

## Architecture

```text
Browser
  |
  v
Traefik (rootless Podman, systemd/Quadlet)
  |-- code.example.com ----------> FastAPI control plane :8010 (configurable)
  `-- w-<workspace>.example.com --> fresh OpenCode container :4096
                                         |
                                         +-- /workspace (persistent per user/project)
                                         +-- ~/.local/share/opencode (persistent sessions/auth)
                                         +-- ~/.local/state (persistent state)
                                         +-- ~/.cache (ephemeral tmpfs)
                                         `-- ~/.config/opencode (persistent config)

FastAPI control plane
  |-- PostgreSQL: users, slots, workspace leases
  |-- rootless Podman CLI: create/destroy runtime containers
  `-- Traefik file provider: add/remove per-workspace routes
```

Traefik never receives the Podman socket. The trusted control plane runs natively as the rootless service user and invokes the rootless `podman` CLI.

## Why capacity slots instead of reusable running containers?

A user's persistent mounts are fixed when a container is created. Reusing the same running container safely would require copying/switching user state and creates more opportunity for cross-user leakage. This implementation therefore pre-caches the image and maintains a fixed number of **slots**. Claiming a slot creates a clean container; releasing it destroys the container. Persistent files live outside the container.

## Requirements

- Linux with cgroup v2
- Podman 5.x+
- systemd user services / Quadlet
- Python 3.11+
- `openssl`, `rsync`
- DNS A/AAAA records for both `code.example.com` and `*.code.example.com`

Rootless Podman needs subordinate UID/GID ranges. Verify with:

```bash
grep "^$USER:" /etc/subuid /etc/subgid
podman info --format '{{.Host.CgroupsVersion}}'
```

## Install

The installer requires Python 3.11 or newer. It automatically prefers `python3.13`, `python3.12`, or `python3.11` over an older default `python3`. You can also select the interpreter explicitly with `PYTHON_BIN`.

```bash
export BASE_DOMAIN=code.example.com
sudo loginctl enable-linger "$USER"
./scripts/check-host.sh
PYTHON_BIN=python3.11 ./scripts/install.sh
```

The installer now fails early if the systemd user runtime/bus is missing, if the rootless user manager lacks the `cpu`, `memory`, or `pids` cgroup controllers, or if the selected control-plane port is already occupied. The default direct control-plane port is `8010`; override it with `CONTROL_PLANE_PORT=8011` (or another free port).

If your system's default `python3` is older, for example Python 3.9, run:

```bash
PYTHON_BIN=python3.11 ./scripts/install.sh
```

A failed older install may have left a Python 3.9 virtual environment under `$HOME/opt/opencode-multiuser/.venv`. The installer now recreates this virtual environment automatically using the selected Python 3.11+ interpreter.

The installer creates:

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
  traefik/
  traefik-dynamic/
  users/<uuid>/<project>/...
```

### PostgreSQL storage

PostgreSQL uses a Podman-managed named volume (`opencode-postgres-data`) rather than a host bind mount. This avoids rootless ownership failures when the PostgreSQL entrypoint changes `/var/lib/postgresql/data` to the internal `postgres` user (UID/GID 999). Inspect it with:

```bash
podman volume inspect opencode-postgres-data
```

Back up PostgreSQL logically with `pg_dump`/`pg_dumpall`; do not depend on the named volume's internal storage path.

## API quick test

Register:

```bash
curl -sS http://127.0.0.1:8010/auth/register \
  -H 'content-type: application/json' \
  -d '{"username":"cahya","password":"replace-with-a-long-password"}'
```

Start a workspace using the returned JWT:

```bash
TOKEN='...'
curl -sS http://127.0.0.1:8010/workspaces/start \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"project_slug":"rag-project"}'
```

Stop it:

```bash
curl -sS -X POST http://127.0.0.1:8010/workspaces/<workspace-id>/stop \
  -H "authorization: Bearer $TOKEN"
```


## systemd user manager and cgroup delegation

This deployment launches rootless Podman containers from a systemd user service. The service is packaged with `Delegate=yes`, but the parent `user@<UID>.service` must also receive the cgroup controllers needed by `--cpus`, `--memory`, and `--pids-limit`. `scripts/install.sh` checks this before building images. If the check fails, configure `/etc/systemd/system/user@.service.d/delegate.conf` as root as instructed by the installer, restart the numeric `user@<UID>.service`, then log in again.

The rootless runtime directory `/run/user/<UID>` and its `bus` socket must be created by systemd/logind; do not create them manually. Enable lingering for the service account so the user manager survives logout.

## HTTP test mode

The v5 defaults match the current test deployment: TLS is disabled, workspace URLs use `http`, and Traefik listens on 8080/8443. Port 8443 is therefore plain HTTP until `TRAEFIK_TLS=true` and certificates are configured. Internal Traefik-to-OpenCode traffic remains HTTP even after external TLS is enabled.

## Rootless ports 80/443

The sample intentionally uses 8080/8443. Rootless processes normally cannot bind privileged ports. If your security policy allows it, lower the unprivileged-port threshold on the host and then change the Quadlet/static Traefik ports to 80/443. Alternatively keep Traefik rootless on high ports and forward 80/443 at the host/network edge.

## TLS

The example ships with TLS disabled for generated workspace routes (`TRAEFIK_TLS=false`) and `WORKSPACE_SCHEME=http` so the initial deployment is easy to inspect. For production:

1. Configure 80/443 reachability.
2. Configure Traefik ACME in `traefik.yml` or install your organization's certificate.
3. Set `TRAEFIK_TLS=true` and, for ACME, `TRAEFIK_CERT_RESOLVER=letsencrypt`.
4. Set `WORKSPACE_SCHEME=https`.

A wildcard certificate for `*.code.example.com` is ideal for workspace subdomains.

## Important security notes

- Containers are destroyed before slots are reused.
- Each user/project receives separate persistent workspace, OpenCode data, and config directories.
- Runtime containers use `--read-only`, `no-new-privileges`, drop all Linux capabilities, have resource limits, and use writable mounts only for required data.
- Do **not** mount host SSH keys directly. Implement a Git credential broker or short-lived deploy tokens.
- Do **not** expose the Podman API socket to OpenCode containers or Traefik.
- The control-plane API should be behind SSO/OIDC in production. Local username/password is only an MVP.
- Add outbound network policy if source code or secrets must not reach arbitrary Internet destinations.

## Known MVP limitation: OpenCode Basic Auth

Each runtime receives a random `OPENCODE_SERVER_PASSWORD`. The current MVP does not expose that password to the browser. The production UI should proxy OpenCode traffic through the control plane (or an auth sidecar) and inject Basic Auth server-side. This avoids giving the OpenCode runtime credential to the user and is the recommended next implementation step.

## Recommended next steps

1. OIDC/Keycloak/Entra ID instead of local passwords.
2. Authenticated WebSocket/HTTP reverse proxy that injects OpenCode Basic Auth.
3. Project/repository table + GitLab/GitHub clone flow.
4. Automatic idle timeout and lease recovery after host reboot.
5. Admin page for slots, users, active workspaces, quotas and audit events.
6. Per-project container images/devcontainer support.
7. Network egress restrictions and secret broker.

## Container UID/GID

The workspace image uses UID/GID `10001` for the `opencode` user. This avoids the UID 1000 collision with the built-in `node` user in the official Node image. The runtime uses `--userns keep-id:uid=10001,gid=10001` so the rootless host service account maps to that user inside each workspace container.

The OpenCode npm package is installed with `--allow-scripts=opencode-ai` because its postinstall script is required by current npm versions.


## Writable XDG directories with read-only runtime rootfs

Workspace containers keep the image root filesystem read-only. OpenCode also writes to XDG state and cache directories, so each runtime mounts a persistent per-user/project `opencode-state` directory at `~/.local/state` and an ephemeral tmpfs at `~/.cache`. The control plane explicitly sets `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME`, and `XDG_CONFIG_HOME`.

## Changes in v5

- `CONTROL_PLANE_PORT` is configurable (default `8010`) instead of hard-coded to 8000.
- The packaged control-plane service has `Delegate=yes` and explicit user-runtime environment.
- Installation preflights the systemd user bus and required cgroup-v2 controllers.
- The installer detects a busy control-plane port before starting Uvicorn.
- PostgreSQL uses the named `postgres-data.volume` definition from v3.
- Workspace runtime includes persistent XDG data/state/config mounts and ephemeral cache/tmpfs from v4.
- HTTP is the default public scheme while TLS is disabled, and workspace URLs include the non-standard public port.
- OpenCode installation is smoke-tested with `opencode --version` during the image build.
- Podman stderr is preserved in API errors for easier diagnosis.
