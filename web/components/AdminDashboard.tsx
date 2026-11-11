"use client";

import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getAdminOverview } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { FINDING_LABELS, sourceLabel } from "@/lib/types";
import type { AdminOverview, FindingKind } from "@/lib/types";

const pct = (n: number) => `${Math.round(n * 100)}%`;
const ratio = ([a, b]: [number, number]) => (b === 0 ? "n/a" : `${a}/${b}`);

function Tile({
  label,
  value,
  note,
  warn,
}: {
  label: string;
  value: string | number;
  note: string;
  warn?: boolean;
}) {
  return (
    <Card className="gap-1 py-4">
      <CardContent>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={`text-2xl font-semibold ${warn ? "text-destructive" : ""}`}>{value}</p>
        <p className="mt-1 text-xs text-muted-foreground">{note}</p>
      </CardContent>
    </Card>
  );
}

function Table({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            {head.map((h) => (
              <th key={h} className="py-2 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b last:border-0">
              {r.map((c, j) => (
                <td key={j} className="py-2 pr-4">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Panel({
  title,
  help,
  children,
}: {
  title: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <p className="text-sm text-muted-foreground">{help}</p>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default function AdminDashboard() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getAdminOverview()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <Alert variant="destructive" className="mt-6">
        <AlertDescription>Could not load the admin numbers: {error}</AlertDescription>
      </Alert>
    );
  }
  if (!data) return <Skeleton className="mt-6 h-64 w-full" />;

  const { review, edits, claims, llm, evals } = data;
  const reviewers = Object.entries(review.by_reviewer);
  const empty = review.reviews_started === 0 && review.signed_notes === 0;

  return (
    <div className="mt-6 space-y-6">
      {empty && (
        <Alert variant="info">
          <AlertDescription>
            Nothing to show yet. Draft a note, run the review, and sign it, then reload.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Reviews run"
          value={review.reviews_started}
          note={`${review.review_rounds} rounds in all`}
        />
        <Tile
          label="Signed without a review"
          value={review.signed_without_review}
          note={`of ${review.signed_notes} signed notes`}
          warn={review.signed_without_review > 0}
        />
        <Tile
          label="Signed with problems still open"
          value={review.signed_with_open_findings}
          note="the review found something and it was not resolved"
          warn={review.signed_with_open_findings > 0}
        />
        <Tile
          label="Draft changed before signing"
          value={edits.signed_notes === 0 ? "n/a" : pct(edits.mean_edit_rate)}
          note={`${pct(edits.signed_unchanged_share)} signed exactly as written`}
        />
      </div>

      <Panel
        title="Reviewers"
        help="Each reviewer is one node in the review graph. Raised is how many problems it found. Shared means another reviewer found the same problem and the supervisor merged them. Accepted, kept, and rewritten are what the therapist did with the problems in that section, so a reviewer with many kept results is probably noisy."
      >
        {reviewers.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reviews yet.</p>
        ) : (
          <>
            <Table
              head={["Reviewer", "Raised", "Shared", "Accepted", "Kept", "Rewritten", "Kept rate"]}
              rows={reviewers.map(([name, r]) => {
                const answered = r.accepted + r.dismissed + r.rewritten;
                return [
                  sourceLabel(name),
                  r.raised,
                  r.shared,
                  r.accepted,
                  r.dismissed,
                  r.rewritten,
                  answered === 0 ? "n/a" : pct(r.dismissed / answered),
                ];
              })}
            />
            <p className="mt-3 text-xs text-muted-foreground">
              The supervisor merged {review.findings_merged_away} duplicate finding
              {review.findings_merged_away === 1 ? "" : "s"} into one so the therapist sees
              each problem once.
            </p>
          </>
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="What the reviewers found" help="Problems raised, by kind.">
          {Object.keys(review.by_kind).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing yet.</p>
          ) : (
            <Table
              head={["Kind", "Count"]}
              rows={Object.entries(review.by_kind).map(([k, n]) => [
                FINDING_LABELS[k as FindingKind] ?? k,
                n,
              ])}
            />
          )}
        </Panel>
        <Panel
          title="Edits by section"
          help="Average share of the AI draft the therapist changed. A section that is always rewritten points at a prompt or model problem."
        >
          {edits.signed_notes === 0 ? (
            <p className="text-sm text-muted-foreground">No signed notes yet.</p>
          ) : (
            <div className="space-y-2">
              {data.sections.map((s) => (
                <div key={s} className="flex items-center gap-3 text-sm">
                  <span className="w-24 capitalize">{s}</span>
                  <div className="h-2 flex-1 rounded-full bg-muted">
                    <div
                      className="h-2 rounded-full bg-primary"
                      style={{ width: `${Math.round((edits.by_section[s] ?? 0) * 100)}%` }}
                    />
                  </div>
                  <span className="w-10 text-right text-muted-foreground">
                    {pct(edits.by_section[s] ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="Claims"
        help="Where signed notes are in the claim workflow. This app only creates and submits claims. Approval and denial from a payer are not part of it."
      >
        <div className="flex flex-wrap gap-3">
          {(["pending", "created", "submitted", "failed"] as const).map((k) => (
            <Badge key={k} variant={k === "failed" ? "destructive" : "secondary"}>
              {k} {claims[k] ?? 0}
            </Badge>
          ))}
        </div>
      </Panel>

      <Panel
        title="Model calls since the API started"
        help="Calls by purpose. Cost is 0 until you set the price per million tokens, which local models do not need."
      >
        {llm.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No model calls yet. The stub provider does not count as a model call.
          </p>
        ) : (
          <Table
            head={["Purpose", "Calls", "Errors", "Tokens in", "Tokens out", "Cost", "p50 s", "p95 s"]}
            rows={llm.map((r) => [
              r.purpose,
              r.calls,
              r.errors,
              r.input_tokens,
              r.output_tokens,
              `$${r.cost_usd.toFixed(4)}`,
              r.p50_seconds,
              r.p95_seconds,
            ])}
          />
        )}
      </Panel>

      <Panel
        title="Latest eval run per model"
        help="From python -m evals.run. Catch is planted problems found. False alarms are clean notes wrongly flagged."
      >
        {evals.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No saved eval results on this server. Run the evals, then reload.
          </p>
        ) : (
          <Table
            head={["Model", "Date", "Catch (3 reviewers)", "Catch (with rules)", "False alarms", "Unusable", "s per case"]}
            rows={evals.map((e) => [
              `${e.provider}:${e.model}`,
              e.date ? formatDateTime(e.date) : "",
              ratio(e.summary.catch_specialists),
              ratio(e.summary.catch_combined),
              ratio(e.summary.false_alarms_specialists),
              e.summary.unusable_replies,
              e.summary.seconds_per_case.toFixed(1),
            ])}
          />
        )}
      </Panel>

      <Panel title="Recent reviews" help="The latest reviews that were started.">
        {review.recent.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reviews yet.</p>
        ) : (
          <Table
            head={["When", "Note", "Result", "Findings", "Raised by"]}
            rows={review.recent.map((r) => [
              formatDateTime(r.at),
              r.note_id,
              r.status ?? "",
              r.findings,
              r.sources.map(sourceLabel).join(", ") || "none",
            ])}
          />
        )}
      </Panel>
    </div>
  );
}
