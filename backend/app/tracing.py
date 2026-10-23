"""LangSmith tracing. Off unless LANGSMITH_TRACING=true and a key is set.

LangSmith is a hosted service, so by default it never receives note text:
inputs and outputs are hidden and only run names, timings, and ids are sent.
"""
import os

from .config import Settings


def configure_tracing(settings: Settings) -> bool:
    enabled = settings.langsmith_tracing and bool(settings.langsmith_api_key)
    if not enabled:
        os.environ["LANGSMITH_TRACING"] = "false"
        return False
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key or ""
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    # PHI guard: hidden unless someone deliberately turns it off (synthetic data only).
    os.environ["LANGSMITH_HIDE_INPUTS"] = str(settings.langsmith_hide_inputs).lower()
    os.environ["LANGSMITH_HIDE_OUTPUTS"] = str(settings.langsmith_hide_outputs).lower()
    return True
