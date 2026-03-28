from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "hubspot-engine-x"

    DATABASE_URL: str
    SUPER_ADMIN_JWT_SECRET: str
    HUBSPOT_CLIENT_ID: str
    HUBSPOT_CLIENT_SECRET: str
    NANGO_SECRET_KEY: str
    NANGO_BASE_URL: str = "https://api.nango.dev"
    NANGO_PROVIDER_CONFIG_KEY: str = "hubspot"
    ALLOWED_ORIGINS: str = ""  # comma-separated list of allowed CORS origins

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
