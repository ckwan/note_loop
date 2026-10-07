"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getClaim } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Claim, ClaimStatusValue } from "@/lib/types";

const LABELS = {
  pending: { text: "Queued", variant: "secondary" },
  created: { text: "Created", variant: "info" },
  submitted: { text: "Submitted", variant: "success" },
  failed: { text: "Failed", variant: "destructive" },
} as const satisfies Record<ClaimStatusValue, { text: string; variant: string }>;

const POLL_MS = 2000;
const MAX_POLLS = 30;

type Props = {
  sessionId: number;
  onSettled: () => void;
};

export default function ClaimStatus({ sessionId, onSettled }: Props) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [stalled, setStalled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;

    async function poll() {
      polls += 1;
      try {
        const next = await getClaim(sessionId);
        if (cancelled) return;
        setClaim(next);
        if (next && (next.status === "submitted" || next.status === "failed")) {
          onSettled();
          return;
        }
      } catch {
        // Keep polling. A brief API error should not stop the status updating.
      }
      if (cancelled) return;
      if (polls >= MAX_POLLS) {
        setStalled(true);
        return;
      }
      timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, onSettled]);

  const label = LABELS[claim?.status ?? "pending"];
  const settled = claim?.status === "submitted" || claim?.status === "failed";

  return (
    <Card className="gap-3">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Claim</CardTitle>
        <Badge variant={label.variant}>{label.text}</Badge>
      </CardHeader>
      {(claim?.status === "submitted" || claim?.status === "failed" || stalled) && (
        <CardContent className="text-sm">
          {claim?.status === "submitted" && (
            <p className="text-muted-foreground">
              Reference {claim.payer_reference}
              {claim.submitted_at ? `, ${formatDateTime(claim.submitted_at)}` : ""}
            </p>
          )}
          {claim?.status === "failed" && (
            <p className="text-destructive">
              Submission failed after several retries. A person needs to review this claim.
            </p>
          )}
          {stalled && !settled && (
            <p className="text-warning">
              Still not submitted. Check that the Temporal worker is running.
            </p>
          )}
        </CardContent>
      )}
    </Card>
  );
}
