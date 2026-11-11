"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getClaim, resubmitClaim } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Claim, ClaimStatusValue } from "@/lib/types";

const LABELS = {
  pending: { text: "Queued", variant: "secondary" },
  created: { text: "Created", variant: "info" },
  submitted: { text: "Submitted", variant: "success" },
  failed: { text: "Failed", variant: "destructive" },
  approved: { text: "Approved", variant: "success" },
  denied: { text: "Denied", variant: "destructive" },
  manual_follow_up: { text: "Needs follow up", variant: "warning" },
} as const satisfies Record<ClaimStatusValue, { text: string; variant: string }>;

// Fast while the claim is still being created, slow while it waits for a decision.
const FAST_POLL_MS = 2000;
const MAX_FAST_POLLS = 30;
const SLOW_POLL_MS = 20000;
const MAX_SLOW_POLLS = 30;

const RESUBMITTABLE: ClaimStatusValue[] = ["failed", "denied"];

// Nothing more will change without a person acting.
function isSettled(claim: Claim): boolean {
  if (claim.status === "submitted") return claim.remote_status === null; // simulated payer
  return !["pending", "created"].includes(claim.status);
}

type Props = {
  sessionId: number;
  onSettled: () => void;
};

export default function ClaimStatus({ sessionId, onSettled }: Props) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [stalled, setStalled] = useState(false);
  // Bumped after a resubmit, to start polling again.
  const [run, setRun] = useState(0);
  const [resubmitting, setResubmitting] = useState(false);
  const [resubmitError, setResubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let fastPolls = 0;
    let slowPolls = 0;
    let firstOfRun = true;

    async function poll() {
      // Nobody is looking at a hidden tab. Check again when it comes back.
      if (document.hidden) {
        timer = setTimeout(poll, SLOW_POLL_MS);
        return;
      }
      const first = firstOfRun;
      firstOfRun = false;
      let waiting = false;
      try {
        const next = await getClaim(sessionId);
        if (cancelled) return;
        setClaim(next);
        // After a resubmit the old failed or denied status can still be on the server
        // for a moment, so the first polls of a run never count as settled.
        if (next && isSettled(next) && (run === 0 || !first)) {
          onSettled();
          return;
        }
        waiting = next !== null && next.status === "submitted";
      } catch {
        // Keep polling. A brief API error should not stop the status updating.
      }
      if (cancelled) return;
      if (waiting) {
        slowPolls += 1;
        if (slowPolls >= MAX_SLOW_POLLS) return;
        timer = setTimeout(poll, SLOW_POLL_MS);
        return;
      }
      fastPolls += 1;
      if (fastPolls >= MAX_FAST_POLLS) {
        setStalled(true);
        return;
      }
      timer = setTimeout(poll, FAST_POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, onSettled, run]);

  const resubmit = useCallback(async () => {
    setResubmitting(true);
    setResubmitError(null);
    try {
      await resubmitClaim(sessionId);
      setStalled(false);
      setRun((n) => n + 1);
    } catch (e) {
      setResubmitError(e instanceof ApiError ? e.message : "Could not resubmit the claim.");
    } finally {
      setResubmitting(false);
    }
  }, [sessionId]);

  const label = LABELS[claim?.status ?? "pending"];
  const settled = claim ? isSettled(claim) : false;
  const canResubmit = claim !== null && RESUBMITTABLE.includes(claim.status);

  return (
    <Card className="gap-3">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Claim</CardTitle>
        <Badge variant={label.variant}>{label.text}</Badge>
      </CardHeader>
      {(claim?.status !== "pending" || stalled) && (
        <CardContent className="space-y-3 text-sm">
          {claim?.status === "submitted" && (
            <p className="text-muted-foreground">
              Reference {claim.payer_reference}
              {claim.submitted_at ? `, ${formatDateTime(claim.submitted_at)}` : ""}
              {claim.remote_status ? `. Waiting for a decision (${claim.remote_status}).` : ""}
            </p>
          )}
          {claim?.status === "approved" && (
            <p className="text-muted-foreground">The payer approved this claim.</p>
          )}
          {claim?.status === "failed" && (
            <p className="text-destructive">
              Submission failed after several retries. You can try sending it again.
            </p>
          )}
          {claim?.status === "denied" && (
            <p className="text-destructive">
              The payer denied this claim. Sending it again creates a new claim for the same
              note.
            </p>
          )}
          {claim?.status === "manual_follow_up" && (
            <p className="text-warning">
              No decision arrived in time. A person needs to check this claim.
            </p>
          )}
          {stalled && !settled && (
            <p className="text-warning">
              Still not settled. Check that the Temporal worker is running.
            </p>
          )}
          {canResubmit && (
            <Button size="sm" variant="outline" onClick={resubmit} disabled={resubmitting}>
              {resubmitting ? "Sending…" : "Resubmit claim"}
            </Button>
          )}
          {resubmitError && <p className="text-destructive">{resubmitError}</p>}
        </CardContent>
      )}
    </Card>
  );
}
