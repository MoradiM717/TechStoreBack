from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database ---
    database_url: str = "postgresql://techstore:techstore@localhost:5432/techstore"

    # --- Elasticsearch ---
    elasticsearch_url: str = "http://localhost:9200"
    products_index: str = "products"

    # --- Auth ---
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 1 day for admin sessions

    # --- CORS ---
    # Comma-separated list. Defaults cover the two local Next.js dev servers.
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # --- Object storage (S3-compatible) for product images ---
    # When s3_access_key/s3_secret_key are blank the upload router falls back to
    # writing into `media_root` and serving it from /media (fine for local dev).
    s3_endpoint_url: str = "https://s3.amazonaws.com"
    s3_bucket: str = "techstore-media"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_public_base_url: str = ""

    media_root: str = "media"
    public_base_url: str = "http://localhost:8000"

    class Config:
        env_file = ".env"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def s3_enabled(self) -> bool:
        return bool(self.s3_access_key and self.s3_secret_key)


settings = Settings()
