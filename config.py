"""Runtime settings, read from environment variables or a local .env file."""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPOLENS_", extra="ignore")

    # Where cloned repos and the index database live.
    data_dir: Path = Path("data")

    # Filtering
    max_file_bytes: int = 400_000  # larger files are almost always generated/vendored

    # Embeddings run locally through fastembed (ONNX). See services/embed.py.
    embed_model: str = "jinaai/jina-embeddings-v2-base-code"
    embed_max_chars: int = 2_000  # embedding input only; the stored chunk is never truncated

    # Retrieval
    top_k: int = 8
    graph_expand_seeds: int = 3  # how many top files to expand through the import graph
    graph_expand_max: int = 4  # max extra chunks the graph can pull in

    # Answering
    claude_model: str = "claude-opus-5"
    claude_effort: str = "high"
    context_char_budget: int = 60_000  # roughly 15k tokens of code in the prompt

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "repolens.db"


settings = Settings()


def storage_status() -> dict:
    """Where persistent data lives, and whether it's on a Railway volume.

    data_dir holds the real product data (the SQLite index of chunks,
    embeddings, and import graphs, plus ingest jobs), not a cache. On Railway,
    anything outside a Volume is wiped on every redeploy. RAILWAY_SERVICE_ID is
    always set on Railway; RAILWAY_VOLUME_MOUNT_PATH only when a Volume is
    attached.
    """
    data_dir = settings.data_dir.resolve()
    on_railway = "RAILWAY_SERVICE_ID" in os.environ
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    on_volume = None  # not applicable off Railway
    if on_railway:
        on_volume = mount is not None and data_dir.is_relative_to(Path(mount).resolve())
    return {"data_dir": str(data_dir), "on_railway": on_railway, "volume_mount": mount, "persistent": on_volume}
