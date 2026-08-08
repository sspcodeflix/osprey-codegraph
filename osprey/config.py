from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OSPREY_", env_file=".env",
                                      extra="ignore")

    db_dsn: str = ("host=127.0.0.1 port=5434 dbname=osprey "
                   "user=osprey password=osprey")
    # API auth: empty token = dev mode (no auth); set in any real deployment
    api_token: str = ""
    # demo instance: browsing is read-only, direct indexing and doc
    # generation are disabled; visitors file repo requests instead
    demo_mode: bool = False
    # where the built web UI lives; empty = repo-relative default (dev).
    # container images set this explicitly (the package is installed to
    # site-packages there, so repo-relative resolution has no meaning)
    ui_dist: str = ""
    # top-bar identity chip until SSO lands (OSPREY_USER_LABEL)
    user_label: str = "Local Dev"
    # where osprey-mcp (and other API clients) reach the query API
    api_url: str = "http://127.0.0.1:8800"
    # traversal cost bounds (ARCHITECTURE.md §7)
    max_depth: int = 5
    max_nodes: int = 2000
    statement_timeout_ms: int = 5000
    # indexer
    scip_python_cmd: str = "npx --yes @sourcegraph/scip-python"
    scip_typescript_cmd: str = "npx --yes @sourcegraph/scip-typescript"
    index_timeout_s: int = 3600
    worker_poll_s: float = 2.0
    retention_keep: int = 30      # ready snapshots kept per repo (§12)
    # on-the-fly indexing of pasted URLs (§11): host allowlist prevents SSRF
    # to internal services; remote repos are always sandboxed and deps-free
    allowed_git_hosts: str = "github.com,gitlab.com"
    max_repo_mb: int = 500
    remote_force_container: bool = True
    # Ask (chat) provider: local-first by design (§10). 'anthropic' is the
    # cloud opt-in and requires an explicit key.
    chat_provider: str = "ollama"       # ollama | anthropic | deepseek
    chat_model: str = "qwen3:8b"
    ollama_url: str = "http://127.0.0.1:11434"
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    chat_max_steps: int = 8
    # sandbox: 'local' (trusted repos, dev) or 'container' (§11.1)
    executor: str = "local"
    container_runtime: str = "docker"     # or 'podman'
    indexer_image: str = "osprey-indexer:0.1"
    container_memory: str = "8g"
    container_cpus: float = 8.0
    # in-container commands (indexers are pre-installed in the image)
    container_scip_python_cmd: str = "scip-python"
    container_scip_typescript_cmd: str = "scip-typescript"
    # deps-stage registry mirrors; set both in restricted deployments and
    # block other egress at the network layer (docker network / firewall)
    npm_registry: str = ""
    pip_index_url: str = ""


settings = Settings()
