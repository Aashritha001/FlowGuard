"""Runtime configuration. All values come from environment variables; nothing secret is hardcoded."""
import os


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> tuple[str, bool]:
    """Returns (url, ephemeral). Hosted Postgres providers (Vercel/Neon, Supabase, Render) hand out
    postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver named explicitly.
    On Vercel without a database the only writable place is /tmp, which is wiped between instances."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url:
        return url, False
    if os.environ.get("VERCEL"):
        return "sqlite:////tmp/flowguard.db", True
    return "sqlite:///./flowguard.db", False


class Settings:
    def __init__(self) -> None:
        self.database_url, self.storage_ephemeral = _database_url()
        self.on_vercel = bool(os.environ.get("VERCEL"))
        self.secret_key = os.environ.get("FLOWGUARD_SECRET_KEY", "")
        self.env = os.environ.get("FLOWGUARD_ENV", "development")
        self.cookie_secure = _bool("FLOWGUARD_COOKIE_SECURE", self.env == "production" or self.on_vercel)
        self.session_hours = int(os.environ.get("FLOWGUARD_SESSION_HOURS", "8"))
        # OWASP 2023 guidance for PBKDF2-HMAC-SHA256 is 600k iterations.
        # The in-browser preview lowers this (see tools/build_preview.py) purely for speed.
        self.pbkdf2_iterations = int(os.environ.get("FLOWGUARD_PBKDF2_ITERATIONS", "600000"))
        self.ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "")
        self.ollama_timeout = float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "20"))
        self.ai_enabled = _bool("FLOWGUARD_AI_ENABLED", True)
        self.max_upload_bytes = int(os.environ.get("FLOWGUARD_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
        self.login_rate_limit = int(os.environ.get("FLOWGUARD_LOGIN_ATTEMPTS_PER_15MIN", "10"))
        self.embedded = _bool("FLOWGUARD_EMBEDDED", False)  # True only inside the browser preview
        # Business date used for pricing checks (e.g. "job date in the future"). Empty = today's real date.
        # Admins can also fix it in Settings (stored in the database); this environment variable overrides that.
        self.business_date_env = os.environ.get("FLOWGUARD_BUSINESS_DATE", "")
        self.business_date = self.business_date_env
        self.admin_password = os.environ.get("FLOWGUARD_ADMIN_PASSWORD", "")  # first-run admin only


settings = Settings()
