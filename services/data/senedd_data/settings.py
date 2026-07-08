from pathlib import Path
from typing import List, Optional, Dict, Any, Literal
from typing_extensions import Self

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

from senedd_data.model_registry import MODEL_METADATA_REGISTRY
import logging

# Repository root: services/data/senedd_data/settings.py -> parents[3]
ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_PATH = ROOT_DIR / ".env"

class Settings(BaseSettings):
    # --- Database Settings ---
    database_url: str = Field(
        default="sqlite:///./sqlite_database.db",
        alias="DATABASE_URL"
    )

    # --- Read-only consumer role (web app + MCP) ---
    # The pipeline connects as the schema owner (writes). Read-only consumers
    # (the Next.js app and the MCP server) connect through a SELECT-only role so
    # a bug or injection in a read path is contained by the database, not just by
    # code review. ``provision_readonly_role`` creates/refreshes the role from
    # ``readonly_role`` / ``readonly_password``; read-only consumers connect via
    # ``read_database_url`` (falls back to ``database_url`` when no dedicated URL
    # is set, e.g. local sqlite dev without a provisioned role).
    readonly_role: str = Field(default="senedd_ro", alias="READONLY_DB_ROLE")
    readonly_password: Optional[str] = Field(default=None, alias="READONLY_DB_PASSWORD")
    readonly_database_url: Optional[str] = Field(default=None, alias="DATABASE_URL_RO")

    # --- Core Embedding Strategy ---
    embedding_provider: str = Field(
        default="sentence-transformer", 
        alias="EMBEDDING_PROVIDER"
    )
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2", 
        alias="EMBEDDING_MODEL"
    )

    # --- Third-Party Integrations & Gateways ---
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    hf_token: Optional[str] = Field(default=None, alias="HF_TOKEN")
    openai_api_key: Optional[str] = Field(default = None, alias = "OPENAI_API_KEY")
    embed_batch_size: Optional[int] = Field(default = 250, alias = "EMBED_BATCH_SIZE")

    # --- Late-publication artifact watch (Votes/QNR) ---
    # Votes/QNR publish 0–2 days after the transcript (often never, when a session
    # held no votes / had no unreached written questions). After ingesting a
    # transcript we re-check the portal for these artifacts until this many days
    # past the meeting, then give up silently.
    artifact_watch_days: int = Field(default=14, alias="ARTIFACT_WATCH_DAYS")

    # Content-addressed embedding cache (src/embeddings/cache.py). Reuses a vector
    # whenever the exact embedded string + model has been seen before — saving
    # recompute on backfill re-runs and chunking/filter experiments. A dev aid;
    # disable in prod (no re-runs there) to skip the write path and table growth.
    embed_cache_enabled: bool = Field(default=True, alias="EMBED_CACHE_ENABLED")
    # secret_key: str = Field(default="your_secret_key_here", alias="SECRET_KEY") #UNUSED

    # --- Runtime Flags & Controls ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", 
        alias="LOG_LEVEL"
    )
    allowed_hosts: List[str] = Field(default=["*"], alias="ALLOWED_HOSTS") #UNUSED

    # Pydantic Settings Engine Blueprint Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True
    )

    # --- Dynamic Registry-Driven Validation ---
    @model_validator(mode="after")
    def validate_against_registry(self) -> Self:
        """
        Dynamically cross-references EMBEDDING_PROVIDER and EMBEDDING_MODEL 
        against the centralized MODEL_METADATA_REGISTRY.
        """
        provider = self.embedding_provider.lower().strip()
        model = self.embedding_model.lower().strip()
        
        # Normalize naming variations (e.g., handling dash vs. underscore if needed)
        if provider == "sentence-transformer":
            provider = "sentence-transformers"

        # Construct the look-up key used in your registry (e.g., 'openai/text-embedding-3-small')
        # Registry keys are case-sensitive (e.g. 'all-MiniLM-L6-v2'), so match
        # case-insensitively rather than against the lowercased key.
        registry_key = f"{provider}/{model}"
        canonical = {k.lower(): k for k in MODEL_METADATA_REGISTRY}

        if registry_key not in canonical:
            available_keys = ", ".join(MODEL_METADATA_REGISTRY.keys())
            raise ValueError(
                f"The combination '{registry_key}' was not found in the MODEL_METADATA_REGISTRY.\n"
                f"Supported configurations are: [{available_keys}]"
            )

        # Catch specific configuration omissions if a valid model is chosen
        if provider == "ollama" and not self.ollama_url:
            raise ValueError("Ollama provider selected, but OLLAMA_URL is completely blank.")

        return self
    
    @property
    def read_database_url(self) -> str:
        """Connection URL for read-only consumers (MCP, retrieval service).

        Falls back to the primary ``database_url`` when no dedicated read-only
        URL is configured (e.g. local sqlite dev without a provisioned role).
        """
        return self.readonly_database_url or self.database_url

    @property
    def embedding_metadata(self) -> Dict[str, Any]:
        """
        Helper property to easily access active model metadata constraints 
        anywhere in the application via settings.embedding_metadata.
        """
        provider = "sentence-transformers" if self.embedding_provider == "sentence-transformer" else self.embedding_provider
        registry_key = f"{provider}/{self.embedding_model}"
        return MODEL_METADATA_REGISTRY[registry_key]


class SeneddTerminalFormatter(logging.Formatter):
    """Custom CLI formatter that adds colors to levels and cleans up module views."""
    
    # ANSI escape sequences for clean terminal styling
    GREY = "\x1b[38;20m"
    CYAN = "\x1b[36;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    # Define color mappings for log severity
    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A curated pool of clean, highly visible terminal colors (ANSI 256-color codes)
        # Avoids dark blues/blacks that disappear on dark terminal backdrops
        self._palette = [
            13,  # High-intensity Magenta
            14,  # High-intensity Cyan
            208, # Vibrant Orange
            118, # Bright Lime Green
            171, # Medium Purple/Orchid
            214, # Warm Yellow-Gold
            45,  # Turquoise Blue
            165, # Deep Pink/Magenta
        ]
        # Track assignments dynamically: { "module_leaf_name": "\x1b[38;5;...m" }
        self._assigned_colors = {}
        self._palette_index = 0

    def _get_module_color(self, name: str) -> str:
        """Dynamically fetches or registers a unique color sequence for a module name."""
        if name in self._assigned_colors:
            return self._assigned_colors[name]
        
        # Pick the next color code from our rolling wheel pool
        color_code = self._palette[self._palette_index]
        ansi_sequence = f"\x1b[38;5;{color_code}m"
        
        # Save assignment and cycle index pointer safely
        self._assigned_colors[name] = ansi_sequence
        self._palette_index = (self._palette_index + 1) % len(self._palette)
        
        return ansi_sequence

    def format(self, record):
        # Cache original properties we want to augment temporarily
        orig_name = record.name
        orig_levelname = record.levelname
        
        # Colorize the Level tag
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        record.levelname = f"{color}{orig_levelname:<7}{self.RESET}"
        
        # Human-ize module tracking strings
        if orig_name == "orchestrator":
            record.name = f"{self.GREEN}orchestrator{self.RESET}"
        elif orig_name.startswith(("senedd_data.", "senedd_embeddings.", "senedd_search.", "senedd_mcp.")):
            module_leaf = orig_name.split('.')[-1]
            mod_color = self._get_module_color(module_leaf)
            record.name = f"{mod_color}{module_leaf}{self.RESET}"

        # Clean, scannable format layout
        log_fmt = "%(asctime)s [%(levelname)s] (%(name)s): %(message)s"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        
        result = formatter.format(record)
        
        # Restore original properties to minimize downstream context side-effects
        record.name = orig_name
        record.levelname = orig_levelname
        return result


def setup_logging():
    """Initializes system logging on the root logger, bypassing basicConfig redundancy."""
    root_logger = logging.getLogger()
    
    # Safely assign the dynamic structural log level threshold
    root_logger.setLevel(settings.log_level.upper())
    
    # If the root logger doesn't have handlers, bind our color formatter stream
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(SeneddTerminalFormatter())
        root_logger.addHandler(handler)
        
    # Return confirmation via your named orchestrator module logger channel
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("ollama").setLevel(logging.WARNING)
    
    logger = logging.getLogger("senedd_orchestrator")
    logger.debug("Global standard color-coded terminal log pipeline established.")
        
settings = Settings()



