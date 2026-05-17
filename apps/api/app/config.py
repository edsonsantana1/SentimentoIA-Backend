from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações centrais do backend.

    Manutenção:
    - Nunca coloque chaves reais aqui.
        - Use sempre o arquivo apps/api/.env.
        - O Apify foi removido do fluxo principal.
        - Coleta de fontes externas agora usa scraping com BeautifulSoup.
    """

    ENV: str = "development"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, value):
        if isinstance(value, str) and value.lower() in {"release", "prod", "production"}:
            return False
        return value

    # MongoDB Atlas
    MONGODB_URI: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "sentimento_db"

    # LLM oficial do MVP: Ollama Cloud.
    LLM_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "https://ollama.com/api"
    # Legado: mantido para compatibilidade com ambientes antigos.
    OLLAMA_CLOUD_URL: str = ""
    OLLAMA_API_KEY: str = ""
    OLLAMA_MODEL: str = "llama3.1:8b"
    OLLAMA_TIMEOUT_SECONDS: int = 60

    # URL publica do frontend para links transacionais.
    FRONTEND_URL: str = "http://localhost:5173"

    # Recuperacao de senha.
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # SMTP (provedor gratuito suportado via credenciais do usuario).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_FROM_EMAIL: str = "no-reply@sentimentoia.local"
    SMTP_FROM_NAME: str = "SentimentoIA"
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False

    @property
    def SMTP_EFFECTIVE_USERNAME(self) -> str:
        return (self.SMTP_USER or self.SMTP_USERNAME or "").strip()

    @property
    def SMTP_EFFECTIVE_FROM_EMAIL(self) -> str:
        return (self.SMTP_FROM or self.SMTP_FROM_EMAIL or "").strip()

    # Legado: mantido apenas para não quebrar ambientes antigos.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_API_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL: str = "openrouter/free"
    GROK_API_KEY: str = ""
    GROK_API_URL: str = "https://openrouter.ai/api/v1"
    GROK_MODEL: str = "openrouter/free"

    @property
    def LLM_API_KEY(self) -> str:
        return (self.OPENROUTER_API_KEY or self.GROK_API_KEY or "").strip()

    @property
    def LLM_API_URL(self) -> str:
        return (self.OPENROUTER_API_URL or self.GROK_API_URL or "https://openrouter.ai/api/v1").strip()

    @property
    def LLM_MODEL(self) -> str:
        return (self.OPENROUTER_MODEL or self.GROK_MODEL or "openrouter/free").strip()

    @property
    def OLLAMA_EFFECTIVE_MODE(self) -> str:
        return "cloud"

    @property
    def OLLAMA_EFFECTIVE_URL(self) -> str:
        """Retorna base URL normalizada, sem trailing /api para evitar duplicacao.

        Se o usuario configurar OLLAMA_BASE_URL=https://ollama.com/api,
        esta property retorna https://ollama.com para que o servico monte
        /api/generate corretamente.
        """
        configured_url = (
            self.OLLAMA_BASE_URL or self.OLLAMA_CLOUD_URL or "").strip().rstrip("/")
        normalized = configured_url.lower()
        if "localhost" in normalized or "127.0.0.1" in normalized:
            return ""
        # Remove trailing /api para evitar /api/api/generate
        if normalized.endswith("/api"):
            configured_url = configured_url[:-4]
        return configured_url

    # Scraping (POC/MVP)
    SCRAPER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    SCRAPER_DELAY_SECONDS: float = 1.0
    SCRAPER_TIMEOUT_SECONDS: int = 20
    SCRAPER_DEFAULT_LIMIT: int = 10
    SCRAPER_DEFAULT_SOURCES: str = "reclameaqui,reddit,web"
    SCRAPER_MAX_ITEMS_PER_SOURCE: int = 25
    SCRAPER_MAX_TOTAL_ITEMS: int = 75
    SCRAPER_MAX_PAGES_PER_SOURCE: int = 2
    SCRAPER_RETRY_ATTEMPTS: int = 3
    SCRAPER_RETRY_BACKOFF_SECONDS: float = 1.0
    SCRAPER_MIN_TEXT_LENGTH: int = 20
    SCRAPER_RECLAMEAQUI_URL: str = "https://www.reclameaqui.com.br"
    SCRAPER_RECLAMEAQUI_SEARCH_URL: str = "https://www.reclameaqui.com.br/busca/?q="
    SCRAPER_REDDIT_URL: str = "https://www.reddit.com"
    SCRAPER_MASTODON_BASE_URL: str = "https://mastodon.social"
    SCRAPER_MASTODON_SEARCH_PATH: str = "/api/v2/search"
    SCRAPER_MASTODON_ACCESS_TOKEN: str = ""
    SCRAPER_WEB_SEARCH_URL: str = "https://duckduckgo.com/html/"
    SCRAPER_RELEVANCE_THRESHOLD: float = 0.1
    SCRAPER_ENABLE_BROWSER_FALLBACK: bool = False

    # Cache e atualização automática
    CACHE_TTL_MINUTES: int = 30
    AUTO_REFRESH_ENABLED: bool = False
    AUTO_REFRESH_INTERVAL_MINUTES: int = 60

    # Segurança
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ENABLE_DEV_CLEAR_DATA: bool = False
    PUBLIC_ERROR_VERBOSE: bool = False

    # CORS local
    CORS_ORIGINS_CSV: str = ""
    CORS_ORIGINS: list = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @property
    def IS_PRODUCTION(self) -> bool:
        return (self.ENV or "").strip().lower() in {"production", "prod", "release"}

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        value = str(origin or "").strip().rstrip("/")
        if not value:
            return ""
        if not value.startswith(("http://", "https://")):
            return ""
        return value

    @property
    def CORS_ORIGINS_EFFECTIVE(self) -> list[str]:
        """Resolve origens CORS de forma segura para deploy.

        Regras:
        - Sempre considera FRONTEND_URL quando valido.
        - Aceita override por CORS_ORIGINS_CSV (separado por virgula).
        - Em producao, remove localhost/127.0.0.1 automaticamente.
        """
        candidates: list[str] = []

        if isinstance(self.CORS_ORIGINS, list):
            candidates.extend(str(item) for item in self.CORS_ORIGINS)

        csv_origins = [item.strip() for item in str(
            self.CORS_ORIGINS_CSV or "").split(",") if item.strip()]
        candidates.extend(csv_origins)

        if self.FRONTEND_URL:
            candidates.append(self.FRONTEND_URL)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = self._normalize_origin(item)
            if not normalized:
                continue
            lowered = normalized.lower()
            if self.IS_PRODUCTION and ("localhost" in lowered or "127.0.0.1" in lowered):
                continue
            if normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)

        return deduped

    # Limites operacionais
    MAX_TEXT_LENGTH: int = 5000
    BATCH_SIZE: int = 100
    WORKER_POLL_INTERVAL_SECONDS: int = 5
    WORKER_BATCH_SIZE: int = 50
    LLM_TRIGGER_MIN_COMMENTS: int = 1
    LLM_MAX_SAMPLE_MENTIONS: int = 40
    LOG_LEVEL: str = "INFO"

    # NPS
    NPS_COOLDOWN_DAYS: int = 7
    NPS_MIN_INTERACTIONS: int = 5
    NPS_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
