from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://noteloop:noteloop@localhost:5432/noteloop"
    seed_on_start: bool = True

    llm_provider: str = "stub"  # "stub", "anthropic", "ollama", or "bedrock"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = None

    # Ollama runs on the host machine. From inside Docker, use host.docker.internal.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout_seconds: float = 120.0

    # Amazon Bedrock. Copy the model or inference profile ID from the Bedrock console.
    bedrock_model_id: str = ""
    bedrock_region: str = "us-west-2"
    bedrock_timeout_seconds: float = 120.0

    # LangSmith tracing. Off by default. Inputs and outputs hidden by default (PHI).
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "noteloop"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_hide_inputs: bool = True
    langsmith_hide_outputs: bool = True

    redis_url: str = "redis://localhost:6379/0"
    draft_rate_limit_per_minute: int = 5
    suggest_rate_limit_per_minute: int = 20

    # Rounds of check, fix, and therapist decision before the review stops.
    review_max_rounds: int = 2

    # Cost tracking. Prices are US dollars per million tokens for the model you run.
    # They default to 0 because local models are free and prices change. Copy yours
    # from the provider's pricing page.
    llm_price_input_per_mtok: float = 0.0
    llm_price_output_per_mtok: float = 0.0
    # Alerts to Slack. 0 turns a rule off.
    llm_daily_cost_budget_usd: float = 0.0
    llm_daily_token_budget: int = 0
    llm_failure_alert_threshold: int = 3  # model calls failing in a row
    llm_slow_call_seconds: float = 0.0  # one call slower than this
    llm_alert_cooldown_seconds: float = 900.0  # most one slow call alert per this long
    # Reviewers that run in parallel. Use "general" alone for one all purpose reviewer.
    review_reviewers: str = "grounding,safety,billing"
    review_rate_limit_per_minute: int = 10

    temporal_enabled: bool = True
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "noteloop"

    # Most claims per second sent to the payer, across all workers. 0 means no limit.
    payer_max_per_second: float = 5.0
    resubmit_chunk_size: int = 25

    # Slack incoming webhook for monitoring messages. Leave empty to turn Slack off.
    # Treat the URL as a secret.
    slack_webhook_url: str | None = None
    # Also post to Slack when a signed note's claim is submitted and when it is approved.
    # Off by default, because a busy clinic would flood the channel.
    slack_notify_claim_events: bool = False
    # Used to link a message to its workflow.
    temporal_ui_url: str = "http://localhost:8233"

    # When the daily claims summary goes to Slack. Created by `python -m app.schedules`.
    summary_cron: str = "0 9 * * *"
    summary_timezone: str = "America/Los_Angeles"

    # "simulated" keeps the built in fake payer. "adjudication" hands the claim to the
    # Claims Feedback Loop API and tracks the decision.
    payer_mode: str = "simulated"
    claims_api_url: str | None = None
    claims_org_id: str | None = None
    claims_http_timeout_seconds: float = 10.0
    # Seconds between status checks, and the longest total wait before a person is asked
    # to follow up. Polling only: the API has no webhook.
    claims_poll_interval_seconds: float = 30.0
    claims_poll_max_wait_seconds: float = 3600.0
    # The claim has no amount of its own yet, so adjudication uses this one.
    claim_default_amount: float = 150.0

    # Demo switch. The first N submit attempts fail, so you can watch Temporal retry.
    simulate_payer_failures: int = 0

    @model_validator(mode="after")
    def _check_payer_mode(self) -> "Settings":
        if self.payer_mode not in ("simulated", "adjudication"):
            raise ValueError(
                f"PAYER_MODE must be 'simulated' or 'adjudication', got {self.payer_mode!r}"
            )
        if self.payer_mode == "adjudication":
            missing = [
                name
                for name, value in (
                    ("CLAIMS_API_URL", self.claims_api_url),
                    ("CLAIMS_ORG_ID", self.claims_org_id),
                )
                if not (value or "").strip()
            ]
            if missing:
                raise ValueError(f"PAYER_MODE=adjudication requires {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
