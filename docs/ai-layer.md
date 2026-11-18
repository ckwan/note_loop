# AI layer and workflows

How a model call is guarded, validated and scored, and how the review and signing workflows run. Back to the [README](../README.md).

Every model call goes through the same path, whichever model is behind it. The evals run that same path offline, on invented cases, so a change can be scored before it ships.

```mermaid
flowchart TB
    subgraph Live["Live, on every request"]
        IN["Session notes<br/>untrusted text"] --> G1["Input guardrails<br/>size limit, strip control characters,<br/>neutralize prompt tags"]
        G1 --> P["Provider interface<br/>Ollama, Bedrock, Anthropic, or stub"]
        P --> V["Output validation<br/>Pydantic reply models,<br/>one retry, 502"]
        V --> G2["Output guardrails<br/>grounding, safety mention,<br/>injection warning"]
        G2 --> H["Therapist reviews,<br/>edits, and signs"]
        subgraph RV["LangGraph review, three reviewers in parallel"]
            R1["Grounding<br/>reviewer"]
            R2["Safety<br/>reviewer"]
            R3["Billing<br/>reviewer"]
        end
        RV --> P
        RV --> SUP["Supervisor<br/>merge, remove duplicates,<br/>safety first"]
        G2 -->|"rule findings"| SUP
        SUP --> FX["Fixer<br/>text only for<br/>flagged sections"]
        FX --> H
    end
    P -.-> LS["LangSmith traces<br/>names and timings, no note text"]
    P -.-> OBS["Metrics and alerts<br/>tokens, cost, latency,<br/>Slack on budget or failures"]
    subgraph Offline["Offline, before shipping a change"]
        C["Synthetic cases<br/>drafts and planted problems"] --> E["Eval runner<br/>python -m evals.run"]
        E --> P
        E --> G2
        E --> T["Score table and JSON<br/>per model and digest"]
        T --> CI["CI gate<br/>fails the build if a<br/>score drops"]
    end
```

The guardrails sit on both sides of the model and are plain code, so they behave the same on every model. In the review, the rules feed the same supervisor as the model reviewers. The evals score one reviewer, three specialists, and three specialists plus the rules separately, which shows how much each part catches.

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
    par Grounding reviewer
        G->>L: Facts against the rough session notes
    and Safety reviewer
        G->>L: Risk of harm is documented
    and Billing reviewer
        G->>L: Each section can support a claim
    end
    G->>G: Supervisor merges findings and adds the rule based checks
    G->>L: Propose fixes for flagged sections
    G->>D: Save a checkpoint and pause
    A-->>U: Findings and proposed fixes
    U->>A: Use a fix, keep the text, or rewrite each section
    A->>G: Resume with the answers
    G->>L: Run the three reviewers again, up to two rounds
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
