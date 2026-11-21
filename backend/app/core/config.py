from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    similarity_service_url: str
    notify_service_url: str
    secret_key: str
    upload_size_limit_mb: int = 10

    class Config:
        env_file = ".env"

settings = Settings()
