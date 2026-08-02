from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV = Path(__file__).resolve().parents[1] / ".env"

class Settings(BaseSettings):
    app_name: str = "KiranaSaathi"
    database_url: str = "sqlite:///./kirana_saathi.db"
    jwt_secret: str = "change-this-in-production"
    access_token_minutes: int = 60
    otp_ttl_seconds: int = 300
    otp_dev_code: str = "123456"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    admin_username: str = "id"
    admin_password: str = "root"
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    model_config = SettingsConfigDict(env_file=BACKEND_ENV, env_prefix="KIRANA_", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

settings = Settings()
