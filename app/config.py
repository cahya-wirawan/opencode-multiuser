from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_domain: str = "code.example.com"
    # Public URL reached through Traefik. This is also the single URL used for
    # opening workspaces; no per-workspace hostnames are required.
    control_plane_url: str = "http://code.example.com:8443"
    control_plane_bind: str = "0.0.0.0"
    control_plane_port: int = 8010
    traefik_entrypoint: str = "websecure"

    database_url: str = "sqlite:///./opencode-multiuser.db"
    jwt_secret: str = "development-only-change-me"
    jwt_ttl_minutes: int = 480
    allow_registration: bool = True
    session_cookie_name: str = "oc_session"
    workspace_cookie_name: str = "oc_workspace"
    # Explicit cookie policy. Keep this independent of CONTROL_PLANE_URL so an
    # upgrade from an older HTTPS URL cannot accidentally mark cookies Secure
    # while Traefik is serving plain HTTP.
    cookie_secure: bool = False

    podman_bin: str = "/usr/bin/podman"
    workspace_image: str = "localhost/opencode-workspace:latest"
    podman_network: str = "opencode-net"
    data_root: str = "~/.local/share/opencode-multiuser"
    max_slots: int = 10
    workspace_memory: str = "8g"
    workspace_cpus: float = 4.0
    workspace_pids_limit: int = 1024
    # Slot N publishes container :4096 only on host loopback at base + N.
    # Example: base 41000, slot 6 -> 127.0.0.1:41006.
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



settings = Settings()
