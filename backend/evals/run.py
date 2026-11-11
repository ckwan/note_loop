"""Run the evals.

    python -m evals.run --provider stub
    python -m evals.run --provider ollama --models llama3.2:3b,llama3.1:8b,qwen2.5:7b
    python -m evals.run --provider anthropic
    python -m evals.run --provider bedrock --models <model or inference profile id>

Results are saved as JSON under evals/results/ and printed as a markdown table.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import metrics
from app.config import get_settings
from app.llm import AnthropicProvider, BedrockProvider, OllamaProvider, StubProvider

from .scoring import run_all, summarize

RESULTS = Path(__file__).parent / "results"


def ollama_digest(base_url: str, model: str) -> str | None:
    """The model's digest, so the results say exactly which weights ran. Best effort."""
    try:
        tags = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5).json()
        for item in tags.get("models", []):
            if item.get("name") == model:
                return item.get("digest")
    except Exception:
        pass
    return None


def build(provider: str, model: str | None):
    settings = get_settings()
    if provider == "stub":
        return StubProvider(), "stub", None
    if provider == "ollama":
        name = model or settings.ollama_model
        return (
            OllamaProvider(settings.ollama_base_url, name, settings.ollama_timeout_seconds),
            name,
            ollama_digest(settings.ollama_base_url, name),
        )
    if provider == "anthropic":
        name = model or settings.llm_model
        return AnthropicProvider(settings.anthropic_api_key, name), name, None
    if provider == "bedrock":
        name = model or settings.bedrock_model_id
        return (
            BedrockProvider(name, settings.bedrock_region, settings.bedrock_timeout_seconds),
            name,
            None,
        )
    raise SystemExit(f"Unknown provider {provider!r}. Use stub, ollama, anthropic, or bedrock.")


def pct(pair: tuple[int, int]) -> str:
    done, total = pair
    return "n/a" if total == 0 else f"{done}/{total}"


def table(rows: list[dict]) -> str:
    """Two tables: how well each model writes drafts, and how well it reviews them."""
    drafts = [
        "| Model | Valid | Grounded | Keeps facts | Fills plan | Keeps safety | Ignores injection |",
        "|" + "---|" * 7,
    ]
    reviews = [
        "| Model | Catch: 1 reviewer | Catch: 3 specialists | Catch: 3 specialists + rules "
        "| False alarms: 1 reviewer | False alarms: 3 specialists | False alarms: 3 specialists + rules | Sec/case |",
        "|" + "---|" * 8,
    ]
    for r in rows:
        s = r["summary"]
        c = s["draft_checks"]
        drafts.append(
            f"| {r['model']} | "
            + " | ".join(
                pct(tuple(c[k]))
                for k in ("valid_output", "grounded", "keeps_facts", "fills_sections", "keeps_safety", "ignores_injection")
            )
            + " |"
        )
        reviews.append(
            f"| {r['model']} | "
            + " | ".join(
                pct(tuple(s[k]))
                for k in (
                    "catch_general", "catch_specialists", "catch_combined",
                    "false_alarms_general", "false_alarms_specialists", "false_alarms_combined",
                )
            )
            + f" | {s['seconds_per_case']:.1f} |"
        )
    fixes = [
        "| Model | Valid | Resolves the problem | Grounded after fix | Keeps content | Leaves unfixable alone |",
        "|" + "---|" * 6,
    ]
    for r in rows:
        c = r["summary"]["fix_checks"]
        fixes.append(
            f"| {r['model']} | "
            + " | ".join(
                pct(tuple(c[k]))
                for k in ("valid_output", "resolves", "invents_nothing", "keeps_content", "leaves_unfixable")
            )
            + " |"
        )
    usage = [
        "| Model | Model calls | Failed | Input tokens | Output tokens | Est. cost | p50 sec | p95 sec |",
        "|" + "---|" * 8,
    ]
    for r in rows:
        u = r.get("usage") or {}
        if not u.get("calls"):
            usage.append(f"| {r['model']} | 0 | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        usage.append(
            f"| {r['model']} | {u['calls']} | {u['errors']} | {u['input_tokens']:,} | {u['output_tokens']:,} "
            f"| ${u['cost_usd']:.2f} | {u['p50_seconds']:.1f} | {u['p95_seconds']:.1f} |"
        )
    return (
        "Drafts\n\n" + "\n".join(drafts)
        + "\n\nReviews\n\n" + "\n".join(reviews)
        + "\n\nFixes\n\n" + "\n".join(fixes)
        + "\n\nCost and latency\n\n" + "\n".join(usage)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NoteLoop evals.")
    parser.add_argument("--provider", default="stub", choices=["stub", "ollama", "anthropic", "bedrock"])
    parser.add_argument("--models", help="Comma separated model names to compare")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--price-in", type=float, help="USD per million input tokens, for the cost column")
    parser.add_argument("--price-out", type=float, help="USD per million output tokens, for the cost column")
    args = parser.parse_args()

    settings = get_settings()
    if args.price_in is not None:
        settings.llm_price_input_per_mtok = args.price_in
    if args.price_out is not None:
        settings.llm_price_output_per_mtok = args.price_out
    models = args.models.split(",") if args.models else [None]
    rows = []
    for model in models:
        provider, name, digest = build(args.provider, model)
        print(f"Running {args.provider}:{name} ...", file=sys.stderr)

        def progress(kind: str, case: str, name=name) -> None:
            print(f"  {name} {kind} {case}", file=sys.stderr)

        first_call = metrics.total_calls()
        drafts, reviews, fixes = run_all(provider, progress)
        usage = metrics.summarize_calls(metrics.calls_since(first_call))
        row = {
            "provider": args.provider,
            "model": name,
            "digest": digest,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "summary": asdict(summarize(drafts, reviews, fixes)),
            "usage": usage,
            "drafts": [asdict(d) for d in drafts],
            "reviews": [asdict(r) for r in reviews],
            "fixes": [asdict(f) for f in fixes],
        }
        rows.append(row)
        if not args.no_save:
            RESULTS.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = name.replace(":", "-").replace("/", "-")
            (RESULTS / f"{stamp}-{args.provider}-{safe}.json").write_text(json.dumps(row, indent=2))

    print(table(rows))


if __name__ == "__main__":
    main()
