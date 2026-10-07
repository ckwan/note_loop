from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://noteloop:noteloop@localhost:5432/noteloop"
    seed_on_start: bool = True

    llm_provider: str = "ollama"  # "ollama" or "stub"
    llm_model: str = "llama3.2"
    ollama_host: str = "http://localhost:11434"

    redis_url: str = "redis://localhost:6379/0"
    draft_rate_limit_per_minute: int = 5

    temporal_enabled: bool = True
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "noteloop"

    # Demo switch. The first N submit attempts fail, so you can watch Temporal retry.
    simulate_payer_failures: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
