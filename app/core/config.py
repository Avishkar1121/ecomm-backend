from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str

    access_token_minutes: int = 15
    refresh_token_days_remember: int = 30   # "Remember me" ticked
    refresh_token_days_session: int = 1     # not ticked (browser-session cookie)

    cookie_secure: bool = False             # set True behind HTTPS in production
    cors_origins: list[str] = ["http://localhost:4200"]
    frontend_url: str = "http://localhost:4200"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()