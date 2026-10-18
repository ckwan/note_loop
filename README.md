# NoteLoop

An AI assisted progress note tool for therapists. The AI drafts a SOAP note from rough session notes, the therapist edits it, asks for better wording where needed, and signs it. Signing starts a durable background workflow that creates and submits an insurance claim.

## What it does

1. A therapist opens a session from the schedule and pastes rough notes.
2. The API asks an LLM for a SOAP note (Subjective, Objective, Assessment, Plan) and validates the reply against a schema before saving it.
3. The therapist edits any section. A section reduced to short phrases can be sent back for a more formal rewrite. The suggestion appears as a diff, and nothing changes until the therapist accepts it.
4. Before signing, the therapist can run a review. A LangGraph workflow checks the note against the rough session notes for empty sections, facts the notes do not support, and risk language that was never documented. It proposes fixes, pauses for the therapist to accept, dismiss, or rewrite each one, and checks again, for up to two rounds. The review only returns proposed text. It never changes the saved note. In the web app it is a panel above the editor. For each flagged section the therapist uses the proposed fix, keeps the text as written, or rewrites it. Accepted fixes land in the editor, marked AI assisted in the audit trail, and are saved like any other edit. A paused review is restored after a reload if the text it checked was saved.
5. A compare view shows the AI draft against the final text, word by word.
6. Signing locks the note, writes an audit event, and starts a Temporal workflow that creates a claim and submits it to a simulated payer, with retries.
7. A batch workflow can resubmit every failed claim in chunks, at a controlled rate.

## System design

```mermaid
flowchart LR
    subgraph Browser
        UI["Next.js app<br/>shadcn/ui"]
    end
    subgraph API["FastAPI service"]
        R["Routes"] --> S["Service layer"]
        S --> RG["Review graph<br/>LangGraph"]
    end
    subgraph Data["Data"]
        DB[("Postgres<br/>notes, claims, audit,<br/>review checkpoints")]
        RD[("Redis<br/>rate limits,<br/>idempotency keys")]
    end
    subgraph Temporal["Temporal"]
        T["Server"]
        SCH["Daily summary<br/>schedule"] --> T
        T --> W1["Worker<br/>workflows and internal work"]
        T --> W2["Worker<br/>payer queue, rate limited"]
    end
    subgraph Outside["Outside services"]
        LLM["LLM provider<br/>Ollama, Anthropic, or stub"]
        SL["Slack<br/>incoming webhook"]
    end
    UI -->|"/api proxy"| R
    API --> Data
    API -->|"start workflow"| T
    API -->|"draft, suggest, review"| LLM
    API -->|"review needs attention"| SL
    Temporal --> Data
    W1 -->|"failures, batch results, daily summary"| SL
```

Two kinds of workflow run side by side. LangGraph runs the review before signing, inside the API, and pauses for the therapist. Temporal runs everything after signing, in separate workers.

What happens when a therapist reviews a note:

```mermaid
sequenceDiagram
    participant U as Therapist
    participant A as API
    participant G as Review graph
    participant L as LLM
    participant D as Postgres
    participant S as Slack
    U->>A: Review the text on screen
    A->>G: Start a review
    G->>L: Check the note against the rough session notes
    G->>L: Propose fixes for flagged sections
    G->>D: Save a checkpoint and pause
    A-->>U: Findings and proposed fixes
    U->>A: Use a fix, keep the text, or rewrite each section
    A->>G: Resume with the answers
    G->>L: Check again, up to two rounds
    A-->>U: Ready, or needs attention
    A->>S: Message if problems are still open
    Note over U,D: The review never saves the note. The therapist saves.
```

What happens when a note is signed:

```mermaid
sequenceDiagram
    participant U as Therapist
    participant A as API
    participant D as Postgres
    participant T as Temporal
    participant W as Workers
    U->>A: Sign note with an idempotency key
    A->>D: Lock note, add audit event, add pending claim
    A->>T: Start the note signed workflow
    A-->>U: Signed
    T->>W: prepare claim
    W->>D: Claim created
    T->>W: submit claim on the payer queue
    W->>D: Claim submitted, audit event written
    U->>A: Poll claim status
```

## Tech stack

* Frontend: Next.js, React, TypeScript, Tailwind, shadcn/ui
* Backend: Python 3, FastAPI, SQLAlchemy, Pydantic
* Storage: Postgres, Redis
* Workflows: Temporal with the Python SDK for claims, and LangGraph with a Postgres checkpointer for the pre sign review
* AI: an LLM behind a provider interface (local Ollama, Anthropic, or a deterministic stub for tests), LangGraph for the review loop, LangSmith for optional tracing
* Local setup: Docker Compose

## Why I built this

Therapists spend a large share of their week on documentation. A progress note has to be structured and accurate, and the clinician who ran the session has to sign it. An LLM can write a first draft in seconds, but in healthcare a draft that nobody reviews is a liability.

I wanted to build the smallest realistic version of an AI feature that gives clinicians time back and keeps them responsible for the record. The model call is the easy part. Most of the work is the system around it: validation, review, an audit trail, and a way to see what clinicians change.

I also wanted to learn LangGraph by using it where it fits. Checking a note before signing calls a model and then has to stop and wait for a person, sometimes more than once, which is what a graph with saved state is good at. Temporal already handles the work after signing, so the two tools each get the job they suit. I did not make it an autonomous agent. The steps are fixed, and the therapist makes every decision.

I also wanted to build the whole stack myself, with the schema at one end and the browser screen at the other, plus a background job behind it. My day job is in claims and payments, so the part after signing is modeled on that world: a claim that can fail and be retried, and failed claims that can be resubmitted in bulk. A Slack channel shows how the system is doing, because a claim that fails quietly is worse than one that fails loudly.

## Design decisions

* The human always signs. The AI drafts and suggests, but nothing is applied or final until a therapist accepts it or signs. The AI draft is stored separately and never changed, so comparing it with the signed text shows how much people had to fix.
* Guardrails are code, not prompts. A prompt asking the model not to invent drugs is a request, and a rule that checks the output is a guarantee about what gets flagged. They run on every model, so swapping Ollama for Claude does not change what is caught.
* Model output is validated. Replies must match a schema, get one retry, and are rejected with a 502 if still invalid. Both LLM calls sit behind a provider interface with a stub, so tests are repeatable and the app runs with no API key.
* Temporal runs the work after signing, because the steps can fail partway. It gives durable state, retries with backoff, and a history of every attempt. Activities are idempotent, and the workflow ID comes from the note, so one note cannot start two workflows. The cost is more concepts and one more service than Celery.
* The pending claim is written in the same transaction as the signature. If Temporal is down, the claim stays visibly pending and the follow up work is not lost.
* Batch resubmit uses one child workflow per claim, processed in chunks with an id cursor, and the parent restarts itself with `continue_as_new` to keep its history small. Payer calls run on their own rate limited queue, so one limit covers a single claim and a batch of thousands.
* The pre sign review is a LangGraph workflow, not an agent. The steps are fixed: check, route, propose fixes, pause for the therapist, check again, for up to two rounds. State is saved in Postgres, so a paused review survives an API restart. It returns proposed text and never edits the saved note.
* LangGraph and Temporal do different jobs. LangGraph controls an LLM step that has to wait for a person. Temporal runs the business process after signing. Each is weak where the other is strong, so I kept them apart.
* Slack is for the people running the system. Messages carry ids, statuses, and counts, never patient names or note text. Failures and batch results are posted and successes are not, so a batch of 10,000 does not flood the channel. A failed Slack call is retried and then dropped, so it cannot fail a claim.
* Redis rate limits the LLM endpoints and stores idempotency keys for signing. It fails open, because the database still blocks a double sign.

## Run it locally

```bash
cp .env.example .env
docker compose up --build
```

* App: http://localhost:3000
* API docs: http://localhost:8000/docs
* Temporal UI: http://localhost:8233

`.env.example` sets `LLM_PROVIDER=ollama`, so the model runs on your machine and note text never leaves it. Install [Ollama](https://ollama.com), run `ollama pull llama3.1:8b`, and keep it running. The containers reach it at `host.docker.internal:11434`. The code default and the tests use `stub`, so nothing needs a model or a key to run the test suite. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` to use Claude through the API instead. An unknown provider name fails at startup instead of falling back to the stub.


The Temporal worker does not hot reload. After changing workflow or activity code, run `docker compose restart worker`.

### Try the batch resubmit

1. In `.env`, set `SIMULATE_PAYER_FAILURES=0` so submissions succeed.
2. Create synthetic failed claims: `docker compose exec api python -m app.demo_batch 200`
3. Start the batch: `curl -X POST localhost:8000/claims/resubmit-failed`
4. Watch progress with `curl localhost:8000/claims/summary`, and open the Temporal UI to see the parent workflow and its child workflows.

Set `SIMULATE_PAYER_FAILURES` to a higher number to watch retries, or `PAYER_MAX_PER_SECOND` to a low number to watch the rate limit.

### Guardrails

Rule-based checks run on every AI draft and every review, whatever model wrote the text (`backend/app/guardrails.py`):

* **Grounding.** A diagnosis, medication, dose, or quote that appears in the note but not in the session notes is flagged. Drafts show a warning banner, and the review raises the same finding even when the model reviewer misses it.
* **Safety mention.** If the session notes mention risk of harm and the note never does, it is flagged.
* **Prompt injection.** Session notes are untrusted text. They sit inside tags the prompt marks as data, text that tries to close those tags is neutralized, and instruction-like phrases such as "ignore previous instructions" raise a warning.
* **Input limits.** Notes are 20 to 8000 characters, and control characters are removed.

The audit log records the section and kind of each flag and never the message text. These checks are plain rules and catch common failures. They are not a clinical safety control, and a therapist still reviews and signs every note.

### Tracing with LangSmith

LangSmith shows each review run as a trace: every graph node, every model call, how long each took, and where one failed. It is off by default. To turn it on, set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`.

LangSmith is a hosted service, so the app hides inputs and outputs unless you change `LANGSMITH_HIDE_INPUTS` and `LANGSMITH_HIDE_OUTPUTS`. With the defaults it receives run names, timings, and errors, and no note text. Only turn the hide flags off with synthetic notes. A self-hosted option such as Langfuse or Phoenix would be the next step for real data.

### Slack monitoring

Slack is a monitoring channel for whoever runs the system. It is not part of the therapist's screen.

1. In Slack, create an app, turn on Incoming Webhooks, and add a webhook to a channel.
2. Put the URL in `.env` as `SLACK_WEBHOOK_URL=...` and restart the api and worker. Treat the URL as a secret. With no URL set, nothing is sent.
3. Create the daily summary schedule once: `docker compose exec worker python -m app.schedules`

Messages are sent for a claim that fails after all its retries, a batch resubmit starting and finishing (with counts), a review that ends with open findings, and a daily summary of claims by status. A successful claim sends nothing, so a batch of 10,000 does not flood the channel. Each Temporal message links to its workflow in the Temporal UI.

A message never contains a patient name or any note text. Only ids, statuses, and counts. A failed Slack call is retried a few times and then dropped. It never fails a claim, a batch, or a review.

## Tests

```bash
cd backend
pip install -r requirements.txt
pytest
```

The suite covers the note flow, validation and retry of model output, wording suggestions, every branch of the review graph and its API, rate limiting, idempotent signing, Slack messages, the Temporal activities, and the batch endpoints. The end to end workflow tests need a Temporal test server. They skip when the SDK cannot download one. To run them against a local Temporal CLI, set `TEMPORAL_DEV_SERVER_PATH` to the `temporal` binary.

## Responsible AI and PHI

* This is a portfolio project. It is not a medical device and has not been clinically validated.
* All patient data is synthetic. Do not enter real patient information.
* Drafts are labeled as AI generated until a therapist signs.
* Slack messages carry ids, statuses, and counts only. Never patient names or note text.
* A production version would need a business associate agreement with the LLM vendor, encryption at rest and in transit, role based access control, retention rules, and a security review.

## Known limitations

* There is no authentication. The audit trail attributes actions to the session owner.
* Tables are created at startup. There are no Alembic migrations yet.
* The payer is simulated, and the claim is a stub record, not a real claim format.
* The Temporal dev server in Docker Compose keeps history in memory, so restarting it clears workflow history.
* The batch resubmit has an API endpoint but no button in the UI.
* Wording suggestions and the review have not been evaluated on a real model with clinician review. With the stub provider, the review is a set of simple rules and not real analysis. It is not a clinical safety control.
* Slack messages have only been tested against a mock server. The first real webhook may need small formatting changes.
* The review runs in the API process. Adding steps to a Temporal workflow that is already running would need `workflow.patched` versioning in production.

## Future improvements

1. Evaluation: a set of synthetic sessions that checks drafts for missing sections and invented details, with edit distance against hand corrected references, run as a rate limited batch workflow.
2. Split the review into specialist reviewers (grounding, safety, billing) that run in parallel under a supervisor, then add a setting that requires a review before signing.
3. Feed accepted and rejected suggestions back to the model as examples.
4. Authentication, role based access control, and Postgres row level security.
5. Alembic migrations, and a Temporal setup with persistent storage.
6. Real claim generation and status tracking, with a payer acknowledgment signal and a timeout that moves a claim to manual review.
7. A UI for the batch resubmit with live progress, and a Slack button that starts it. The button needs Slack interactivity, request signature checks, and an allow list of users.
8. Errors and tracing through Sentry and Datadog, including LLM latency and cost.
9. Streaming drafts to the browser.
10. More note formats (DAP, BIRP) and treatment plans.
