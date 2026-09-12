# Changelog

All notable changes to **OpenCode Multiuser** are documented here.

The project follows [Semantic Versioning](https://semver.org/) using `MAJOR.MINOR.PATCH`. The historical v6.x development lineage predates the centralized version file; version **6.8.0** is the first release where the application, package metadata, API, UI, installer, and release archive all read from one version source.

## 6.9.0

### Added

- Persistent `OPENCODE_VERSION` configuration in `control-plane.env`.
- Admin **System & OpenCode** page at `/admin/system`.
- Display of portal version, configured OpenCode version, installed workspace-image version, workspace image name, and upstream registry URL.
- Admin-only upstream version check against the configured npm registry endpoint.
- Admin-only background OpenCode update workflow with live status and build-log polling.
- Candidate image build under a version-specific tag before activation.
- Automated `opencode --version` smoke test before the production `latest` image is changed.
- Persistent update of `OPENCODE_VERSION` only after a successful candidate build and smoke test.
- CLI documentation for manually building, testing, activating, and persisting an OpenCode engine update.
- `OPENCODE_REGISTRY_URL` and `OPENCODE_UPDATE_TIMEOUT_SECONDS` configuration.

### Changed

- `install.sh` now preserves the configured OpenCode engine version across portal upgrades unless `OPENCODE_VERSION` is explicitly supplied.
- OpenCode engine updates no longer require editing both `Containerfile.workspace` and `install.sh`.
- Running workspace containers are intentionally left untouched by an engine update; newly created/restarted workspaces use the activated image.
- Admin User Management now links to the System page.

### Safety

- OpenCode target versions are validated as semantic versions and are never interpolated through a shell command.
- The active workspace image is retagged only after the candidate image successfully reports the requested OpenCode version.
- Failed builds/tests leave the previous `localhost/opencode-workspace:latest` image and persisted configured version unchanged.

## 6.8.0

### Added

- Centralized application version in `app/version.py`.
- Dynamic Python package version derived from the same source of truth.
- `/version` endpoint.
- Version reporting in `/healthz`, FastAPI/OpenAPI metadata, portal UI, and installer output.
- `scripts/version.py` for `show`, `set`, and semantic `patch` / `minor` / `major` bumps.
- `scripts/package-release.sh` for reproducible versioned ZIP archives and SHA-256 checksums.

### Changed

- Formalized Semantic Versioning while continuing the existing v6.x release lineage.
- Release artifact naming now follows `opencode-multiuser-<version>.zip`.
- Reorganized documentation so README focuses on product/operations documentation and CHANGELOG owns detailed release history.

## 6.7.1

### Fixed

- Corrected poor contrast on the Login and Registration hero panels.
- Changed hero foreground typography from white/light text to dark navy tones suitable for the light-blue background.
- Adjusted supporting copy, labels, branding, feature cards, and footer text to maintain accessible contrast consistently.

## 6.7.0

### Added

- Admin-only **User management** console at `/admin/users`.
- Admin navigation entry visible only to administrators.
- User search and filtering by username, display name, email, role, and enabled/disabled state.
- Authentication-source visibility for Local versus OIDC users.
- Created time, last login, account role, account status, active workspace, and slot information.
- Role promotion from Developer to Admin.
- Role demotion from Admin to Developer.
- Account enable/disable controls.
- Automatic stopping of a disabled user's active workspaces so their slots are freed.
- Ability for admins to stop another user's active workspaces without disabling the account.
- Local-account password reset from the admin console.
- Local-user deletion when the account has no workspace history.
- Automatic migration adding nullable `last_login_at` to existing databases.

### Security

- Admin APIs enforce backend authorization independently of whether the admin UI is visible.
- Administrators cannot disable, demote, or delete their own account.
- The last enabled administrator cannot be disabled, demoted, or deleted.
- Concurrent administrative changes are serialized on PostgreSQL to protect last-admin guarantees.
- OIDC identities are disabled rather than deleted because identity lifecycle belongs to the external provider.

## 6.6.0

### Added

- Generic OpenID Connect authentication.
- OIDC discovery through `OIDC_ISSUER` or explicit `OIDC_DISCOVERY_URL`.
- Authorization Code flow with PKCE `S256`.
- OIDC state/nonce session handling.
- OIDC identity binding by immutable `(issuer, sub)` rather than email or username.
- Optional automatic provisioning for first-time OIDC users.
- `/register` page for local accounts.
- `admin` and `developer` roles.
- First provisioned account automatically receives the Admin role.
- Later accounts default to Developer or `OIDC_DEFAULT_ROLE`.
- Dashboard role display.
- Configuration switches for disabling local authentication and registration after SSO is verified.

### Changed

- Local and OIDC users now share the same portal session model, so workspace ownership and gateway routing do not need separate auth paths.
- Existing installations automatically assign the oldest account as Admin and remaining accounts as Developer.
- `password_hash` became nullable to support OIDC-only accounts.
- Logout clears both portal authentication state and temporary OIDC session state.

### Security

- OIDC access/refresh tokens are not persisted by the portal.
- Bootstrap documentation warns that the first account is privileged and should be created on a trusted/internal network.

## 6.5.0

### Added

- Major enterprise portal UI/UX redesign.
- Responsive split-layout Login page.
- Subtle infrastructure/grid background treatment.
- Workspace cards with clear visual status hierarchy.
- Dashboard summary cards for projects, running workspaces, available slots, and idle timeout.
- Consistent shadcn-inspired card, button, badge, alert, dialog, toast, and loading patterns in the server-rendered FastAPI UI.
- Toast notifications for successful and failed operations.
- Confirmation dialog before stopping a workspace.
- Accessible keyboard focus, disabled states, labels, alerts, and reduced-motion support.
- Local UI stylesheet with no external CDN dependency.

### Changed

- Start Workspace now shows staged feedback such as container creation, service readiness, gateway finalization, and workspace opening.
- Stop Workspace now shows progress while destroying the runtime and releasing the slot.
- Logout now shows progress while stopping active workspaces and freeing slots.
- Portal Stop/Logout actions immediately show busy state before navigation.

### Fixed

- Portal Dashboard action now goes directly to `/dashboard` rather than getting stuck at the intermediate `/_portal/dashboard` path.

## 6.4.4

### Improved

- Added visible blocking progress feedback while starting a workspace.
- Added visible progress feedback while stopping a workspace.
- Added visible progress feedback during logout.
- Portal management widget updates its status immediately when Stop or Logout is pressed.
- Dashboard action in the Portal widget navigates directly to `/dashboard`.

## 6.4.3

### Fixed

- Replaced the legacy unique constraint on `(user_id, project_slug, status)` which incorrectly allowed only one historical `STOPPED` row per user/project/status combination.
- Added an automatic migration that drops the old `uq_activeish_workspace` constraint.
- Added a partial unique index applying only to active states: `STARTING`, `RUNNING`, and `STOPPING`.
- Historical `STOPPED` and `ERROR` rows can now coexist safely.
- `/workspaces` shows only the newest row per project while preserving older history in PostgreSQL.
- Prevented startup reconciliation from crashing with PostgreSQL `UniqueViolation` when stopping historical stale workspaces.

## 6.4.2

### Added

- Startup reconciliation between workspace DB records and actual Podman runtime state.
- Periodic housekeeping reconciliation for `RUNNING`, `STARTING`, and `STOPPING` records.
- Validation that a running container publishes the expected `127.0.0.1:4100x -> 4096` mapping.

### Fixed

- Stale DB records no longer produce raw `502 OpenCode backend unavailable` responses after upgrades/restarts.
- Missing or unreachable workspace containers are automatically released and their slots returned.
- Browser navigation is redirected to the dashboard with a recovery message when a runtime disappears.

## 6.4.1

### Fixed

- Added installer integrity checks ensuring the newly introduced `app/portal.py` exists in both the extracted release tree and installed target.
- Prevented partial upgrades where `gateway.py` imported a Portal module that had not been copied.

## 6.4.0

### Added

- Gateway-injected **Portal** widget inside the OpenCode HTML UI.
- Portal actions for Dashboard, Stop workspace, and Logout.
- Dedicated `/_portal/*` management endpoints.
- Widget CSS and JavaScript served by the gateway without modifying upstream OpenCode.
- Lightweight portal status polling so an already-open OpenCode page can detect that its runtime was reclaimed.

### Changed

- Essential Portal actions use native links/forms so they remain functional even if OpenCode applies restrictive CSP rules.
- Installer explicitly restarts the control-plane service after upgrades so new gateway code becomes active immediately.

## 6.3.0

### Added

- User-friendly idle-timeout behavior.
- Persistent stop-reason markers for `idle`, `logout`, `manual`, and `recycle` stop causes.
- Browser redirect to `/dashboard?reason=idle` after an idle runtime is reclaimed.
- Dashboard notice explaining that the workspace was stopped due to inactivity and its slot was freed.
- API `409` response with machine-readable `workspace_idle_timeout` information for stale non-navigation requests.
- WebSocket close code `4409` for idle-reclaimed workspaces.
- Start action for stopped/error workspace cards so users can resume without retyping the project name.

## 6.2.0

### Added

- Authenticated OpenCode readiness probe during workspace startup.
- Configurable readiness timeout and poll interval.
- Automatic workspace stop on logout.
- Background idle-workspace reaper.
- Configurable idle timeout and reaper interval.
- Persistent `last-activity` tracking per workspace.
- Activity refresh on HTTP requests, streaming chunks, WebSocket connect/messages, workspace start, and workspace open.
- Stale runtime recycling when the DB claims a workspace is running but its container has died.

### Changed

- `/workspaces/start` no longer reports success immediately after Podman says the container is `Up`; it waits for authenticated `/global/health` to respond.
- Failed readiness removes the just-created container and returns the slot instead of exposing a transient backend error.

## 6.1.0

### Fixed

- Decoupled browser cookie security from stale `CONTROL_PLANE_URL` values.
- Added explicit `COOKIE_SECURE` configuration.
- Corrected upgrade behavior where a preserved HTTPS control-plane URL caused Secure cookies to be issued while the portal was still running over HTTP, leading to `Invalid or missing authentication` after login.

## 6.0.0

### Added

- Single-host authenticated gateway replacing per-workspace subdomains.
- One public portal hostname for Login, Dashboard, API, and OpenCode traffic.
- Workspace loopback port allocation using `WORKSPACE_HOST_PORT_BASE + slot_id`.
- Browser workspace selection through validated HttpOnly cookie.
- API workspace selection through `X-OpenCode-Workspace` with ownership validation.
- Server-side OpenCode Basic Auth injection.
- HTTP proxying to workspace runtimes.
- WebSocket proxying to workspace runtimes.
- Minimal Login and Dashboard browser UI.

### Changed

- Removed wildcard DNS requirement for workspace subdomains.
- Removed per-workspace Traefik route files.
- Traefik now forwards the single base host to the FastAPI gateway.
- Workspace containers publish OpenCode only on host loopback.
- Client routing/session headers are stripped before proxying to OpenCode.
- Existing older runtimes can be recycled into gateway mode while retaining persistent data.

## Prototype v5

### Added / Changed

- Consolidated the working deployment into a cleaner baseline after installation debugging.
- Added configurable control-plane port rather than hard-coding port 8000.
- Added `Delegate=yes` to the control-plane systemd service so it can launch resource-limited rootless Podman children.
- Added host preflight checks for the systemd user bus and delegated cgroup controllers.
- Defaulted generated URLs to HTTP while Traefik TLS is disabled.
- Improved Podman stderr propagation in API failures.
- Added OpenCode build-time smoke testing.

## Prototype v4

### Fixed

- Added writable OpenCode XDG state and cache handling while retaining a read-only container root filesystem.
- Persisted `~/.local/state/opencode` per user/project.
- Kept OpenCode cache ephemeral in tmpfs.
- Added explicit XDG environment variables.
- Added improved Podman error output instead of returning only exit code 126.

## Prototype v3

### Fixed

- Replaced PostgreSQL bind-mounted data directory with a Podman-managed named volume.
- Resolved rootless PostgreSQL startup failure where the official image could not `chown /var/lib/postgresql/data` on the host bind mount.

## Prototype v2

### Fixed

- Resolved UID collision with the Node base image by moving the OpenCode container user to UID/GID `10001`.
- Updated rootless `keep-id` mapping to match the container user.
- Explicitly allowed OpenCode's npm postinstall script.

## Initial prototype

### Added

- FastAPI multi-user control plane.
- Local username/password authentication.
- PostgreSQL persistence for users, workspace leases, and capacity slots.
- Rootless Podman workspace containers.
- systemd/Quadlet integration.
- Traefik ingress.
- Fixed-size capacity-slot model.
- Fresh disposable OpenCode runtime per active workspace.
- Persistent per-user/per-project workspace, OpenCode data, and configuration directories.
- Workspace start/stop APIs.
- Resource limits for memory, CPU, and PIDs.
- Read-only runtime root filesystem with explicit writable mounts.
- OpenCode Basic Auth generated per runtime.

### Design decision

The original design used a pool of reusable capacity slots rather than a permanently assigned container per user. A slot represents concurrent capacity; claiming a slot creates a fresh container, and releasing it destroys that container. Persistent state lives outside the runtime so another clean container can later mount the same user/project data.
