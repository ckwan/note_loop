# Cost, tracing and monitoring

Metrics, alerts, traces, edit rate, the admin page and Slack. Back to the [README](../README.md).

Every model call is timed and counted in one place, whichever provider made it. Each call records the provider, the model, what it was for (draft, review by a specific reviewer, fixes), the seconds it took, the tokens the provider reported, and an estimated cost.

* **`GET /metrics`** serves these in Prometheus format: calls by outcome, tokens, estimated cost, a latency histogram, replies rejected by validation, and findings raised by the guardrails. It holds counts and labels only, never note text. There is no login on this API, so keep the path private.
* **Slack alerts** fire from the same numbers, once per problem and not once per call: a daily budget in dollars or tokens, a run of failed calls in a row, and slow calls with a cooldown. Each is off at 0. Messages carry counts, seconds, and the model name. They are sent after the response, including when the request failed, and a bad Slack setting never breaks a request.
* **Prices are yours to set.** `LLM_PRICE_INPUT_PER_MTOK` and `LLM_PRICE_OUTPUT_PER_MTOK` are dollars per million tokens and default to 0, because local models are free and prices change. The token budget works with free models.
* **Evals report it too.** The last table of `python -m evals.run` shows calls, tokens, estimated cost, and p50 and p95 seconds per model, so one reviewer and three specialists can be compared on cost as well as quality. Pass `--price-in` and `--price-out` to price a run.
* **Sample alert rules** for Prometheus are in `ops/prometheus-alerts.yml`.

**What the traces contain.** Besides the model calls, these steps are spans:

| Span | Records | Why |
| --- | --- | --- |
| `generate: <what>` | attempts, outcome (`ok`, `rejected`, `provider_error`), rejection reason codes, provider error type | Shows retries and why a reply was rejected, which the model call spans alone do not |
| `guardrails` | findings by kind | How much the rules catch next to the model reviewers |
| `supervise_merge` | findings in per reviewer, kept, dismissed | Where findings are merged or dropped |
| `claims_api GET/POST` | status code, outcome | Claims API health, without the remote claim id |
| `draft_note` | its run id is stored in the `draft_generated` audit row | Lets the edit rate be scored against the draft's trace |

Every span is tagged with `session_id`, `note_id`, and `review` (or `claim_id` and the workflow id for claims), so you can search LangSmith for one note. At signing, the draft's trace gets feedback scores `edit_rate`, `signed_unchanged`, and one `edit_rate_<section>` per section, so you can filter for the drafts therapists rewrote the most. Feedback is sent in the background and a failure is dropped.

Spans start with empty inputs, and metadata and tags carry only ids, counts, enum values, and short reason codes, because LangSmith does not hide those. A test checks that no span metadata contains note text or a patient name.

LangSmith and `/metrics` do different jobs. LangSmith shows one run in detail. `/metrics` shows the totals over time and drives the alerts.

## Edit rate: how much the therapist changes the draft

The draft is saved once and never changed, and the therapist works on a separate copy, so the two can be compared at any time. NoteLoop turns that into a number: the share of words that were added, removed, or replaced, measured against the longer of the two texts. 0% means the draft was signed as written. 100% means none of it survived.

* **On each note.** The API returns `edit_rate` and a rate for each SOAP section, and the signed banner shows it. The audit row for the signature stores the same numbers and no text.
* **Across notes.** `GET /stats/edits` rolls up signed notes: the mean and median rate, the share signed unchanged, and the mean for each section. Add `?therapist_id=` to filter. A section that is always rewritten points at a prompt or model problem in that section.
* **Over time.** `/metrics` has `noteloop_note_edit_rate` and `noteloop_section_edit_rate` histograms, so a model or prompt change can be compared before and after.
* **What it does not tell you.** A low rate can mean a good draft or a therapist who signs without reading, which is what the review step and the guardrails are for. A high rate can mean a poor draft or a therapist who prefers their own wording. Read it next to the eval scores, not instead of them. With synthetic data the numbers only show that the plumbing works, not how good the drafts are.

## Admin page

`/admin` in the web app shows how the review is behaving across all notes. It has no login, like the rest of the app, so keep it private in any real deployment. It reads `GET /admin/overview`, which works everything out from the audit trail, the claims table, the model call counters, and saved eval results, so it needs no extra storage. Counts and labels only. No note text.

* **Safety net.** Reviews run, notes signed without any review, and notes signed while the review still had open problems. Signing does not require a review today, and the first two numbers show how often that happens.
* **Reviewers.** For the grounding, safety, and billing reviewers and the rules, how many problems each raised, how many were merged with another reviewer's by the supervisor, and what the therapist did with them. A reviewer whose findings are mostly kept as written is probably noisy. Decisions are recorded per section, so each finding in a section is credited with the decision.
* **Edits, claims, and model calls.** The edit rate by section, the claim pipeline counts, and calls, tokens, cost, and p50 and p95 seconds by purpose since the API started.
* **Evals.** The latest saved run for each model, read from `backend/evals/results`. It stays empty on a server that has never run the evals.

## Tracing with LangSmith

LangSmith shows each review run as a trace: every graph node, every model call, how long each took, and where one failed. It is off by default. To turn it on, set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`.

LangSmith is a hosted service, so the app hides inputs and outputs unless you change `LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS`. With the defaults it receives run names, timings, and errors, and no note text. Only turn the hide flags off with synthetic notes. A self-hosted option such as Langfuse or Phoenix would be the next step for real data.

## Slack monitoring

Slack is a monitoring channel for whoever runs the system. It is not part of the therapist's screen.

1. In Slack, create an app, turn on Incoming Webhooks, and add a webhook to a channel.
2. Put the URL in `.env` as `SLACK_WEBHOOK_URL=...` and restart the api and worker. Treat the URL as a secret. With no URL set, nothing is sent.
3. Create the daily summary schedule once: `docker compose exec worker python -m app.schedules`

Messages are sent for a claim that fails after all its retries, a batch resubmit starting and finishing (with counts), a review that ends with open findings, a claim denied by the adjudication service, a claim moved to manual follow up, and a daily summary of claims by status. A successful claim sends nothing by default, so a batch of 10,000 does not flood the channel. Set `SLACK_NOTIFY_CLAIM_EVENTS=true` to also get "Claim submitted" when a signed note's claim is submitted and "Claim approved" when the adjudication service approves it (ids and statuses only). Both steps are behind `workflow.patched`. Each Temporal message links to its workflow in the Temporal UI.

A message never contains a patient name or any note text. Only ids, statuses, and counts. A failed Slack call is retried a few times and then dropped. It never fails a claim, a batch, or a review.
