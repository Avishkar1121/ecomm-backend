from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    jwt_secret: str
    jwt_issuer: str = "markethub"
    
    otp_pepper: str                 # required: app refuses to start without it
    otp_dev_log: bool = False       # local only: print OTPs in the server log

    env: str = "dev"                        # "dev" | "prod"

    access_token_minutes: int = 15
    refresh_token_days_remember: int = 30   # "Remember me" ticked
    refresh_token_days_session: int = 1     # not ticked (browser-session cookie)

    cookie_secure: bool = False             # must be True in prod (HTTPS)
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"   # "none" only if the SPA and API are on different sites
    cors_origins: list[str] = ["http://localhost:4200", "http://127.0.0.1:4200", "http://localhost:1209", "http://127.0.0.1:1209"]
    frontend_url: str = "http://localhost:1209"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    smtp_host: str = ""            # empty = dev mode (code is only logged)
    smtp_port: int = 587           # 587 = STARTTLS, 465 = SSL
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""            # e.g. "MarketHub <you@gmail.com>"
    app_name: str = "MarketHub"
    
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
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true when COOKIE_SAMESITE=none")
        if self.env == "prod" and self.otp_dev_log:
            raise ValueError("OTP_DEV_LOG must be false when ENV=prod (it logs OTPs and reset links)")
        return self

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


settings = Settings()