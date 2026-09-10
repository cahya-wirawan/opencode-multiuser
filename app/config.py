from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_domain: str = "code.example.com"
    control_plane_url: str = "http://127.0.0.1:8000"
    workspace_scheme: str = "https"

    database_url: str = "sqlite:///./opencode-multiuser.db"
    jwt_secret: str = "development-only-change-me"
    jwt_ttl_minutes: int = 480
    allow_registration: bool = True

    podman_bin: str = "/usr/bin/podman"
    workspace_image: str = "localhost/opencode-workspace:latest"
    podman_network: str = "opencode-net"
    data_root: str = "~/.local/share/opencode-multiuser"
    max_slots: int = 10
    workspace_memory: str = "8g"
    workspace_cpus: float = 4.0
    workspace_pids_limit: int = 1024

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
