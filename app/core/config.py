from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_issuer: str = "markethub"

    env: str = "dev"                        # "dev" | "prod"

    access_token_minutes: int = 15
    refresh_token_days_remember: int = 30   # "Remember me" ticked
    refresh_token_days_session: int = 1     # not ticked (browser-session cookie)

    cookie_secure: bool = False             # must be True in prod (HTTPS)
    cors_origins: list[str] = ["http://localhost:4200"]
    frontend_url: str = "http://localhost:4200"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("jwt_secret")
    @classmethod
    def _secret_long_enough(cls, v: str) -> str:
        if len(v.encode()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes")
        return v

    @model_validator(mode="after")
    def _prod_guards(self):
        if self.env == "prod" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when ENV=prod")
        return self

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


settings = Settings()