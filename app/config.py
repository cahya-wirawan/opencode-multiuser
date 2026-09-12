from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_domain: str = "code.example.com"
    control_plane_url: str = "http://code.example.com:8443"
    control_plane_bind: str = "0.0.0.0"
    control_plane_port: int = 8010
    traefik_entrypoint: str = "websecure"

    database_url: str = "sqlite:///./opencode-multiuser.db"
    jwt_secret: str = "development-only-change-me"
    jwt_ttl_minutes: int = 480
    session_cookie_name: str = "oc_session"
    workspace_cookie_name: str = "oc_workspace"
    cookie_secure: bool = False

    # Authentication. Local auth remains available by default so a fresh
    # installation works before an OIDC provider is configured.
    local_auth_enabled: bool = True
    allow_registration: bool = True
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_display_name: str = "Corporate SSO"
    oidc_redirect_uri: str = ""
    oidc_use_pkce: bool = True
    oidc_auto_provision: bool = True
    oidc_default_role: str = "developer"
    oidc_username_claim: str = "preferred_username"
    oidc_email_claim: str = "email"
    oidc_name_claim: str = "name"
    oidc_state_cookie_name: str = "oc_oidc_state"
    oidc_state_ttl_seconds: int = 600

    podman_bin: str = "/usr/bin/podman"
    workspace_image: str = "localhost/opencode-workspace:latest"
    opencode_version: str = "1.18.30"
    opencode_registry_url: str = "https://registry.npmjs.org/opencode-ai/latest"
    opencode_update_timeout_seconds: int = 900
    podman_network: str = "opencode-net"
    data_root: str = "~/.local/share/opencode-multiuser"
    max_slots: int = 10
    workspace_memory: str = "8g"
    workspace_cpus: float = 4.0
    workspace_pids_limit: int = 1024
    workspace_ready_timeout_seconds: int = 45
    workspace_ready_poll_interval_seconds: float = 0.5
    workspace_idle_timeout_minutes: int = 30
    workspace_reaper_interval_seconds: int = 60
    stop_workspaces_on_logout: bool = True
    workspace_host_port_base: int = 41000

    traefik_dynamic_dir: str = "~/.local/share/opencode-multiuser/traefik-dynamic"
    traefik_tls: bool = False
    traefik_cert_resolver: str = "letsencrypt"
    opencode_cors: str = ""

    @property
    def expanded_data_root(self) -> Path:
        return Path(self.data_root.replace("%h", str(Path.home()))).expanduser().resolve()

    @property
    def expanded_traefik_dynamic_dir(self) -> Path:
        return Path(self.traefik_dynamic_dir.replace("%h", str(Path.home()))).expanduser().resolve()

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_enabled and self.oidc_issuer.strip() and self.oidc_client_id.strip())

    @property
    def effective_oidc_discovery_url(self) -> str:
        if self.oidc_discovery_url.strip():
            return self.oidc_discovery_url.strip()
        return self.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"

    @property
    def effective_oidc_redirect_uri(self) -> str:
        if self.oidc_redirect_uri.strip():
            return self.oidc_redirect_uri.strip()
        return self.control_plane_url.rstrip("/") + "/auth/oidc/callback"

    def validate_auth(self) -> None:
        if not self.local_auth_enabled and not self.oidc_configured:
            raise RuntimeError(
                "No authentication method is enabled. Enable LOCAL_AUTH_ENABLED or configure OIDC."
            )
        if self.oidc_enabled and not self.oidc_configured:
            raise RuntimeError(
                "OIDC_ENABLED=true requires at least OIDC_ISSUER and OIDC_CLIENT_ID."
            )


settings = Settings()
