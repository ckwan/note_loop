import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { AuditEvent } from "@/lib/types";

function describe(event: AuditEvent): string {
  switch (event.action) {
    case "draft_generated":
      return "AI draft generated";
    case "edited": {
      const sections = (event.detail.sections as string[] | undefined) ?? [];
      const assisted = (event.detail.ai_assisted as string[] | undefined) ?? [];
      const base = `Edited ${sections.join(", ")}`;
      return assisted.length > 0 ? `${base} (AI assisted: ${assisted.join(", ")})` : base;
    }
    case "suggestion_generated": {
      const sections = (event.detail.sections as string[] | undefined) ?? [];
      return `AI wording suggested for ${sections.join(", ")}`;
    }
    case "review_started": {
      const findings = (event.detail.findings as unknown[] | undefined) ?? [];
      return findings.length === 0
        ? "Review before signing: no problems found"
        : `Review before signing: ${findings.length} finding${findings.length === 1 ? "" : "s"}`;
    }
    case "review_answered": {
      const accepted = (event.detail.accepted as string[] | undefined) ?? [];
      const dismissed = (event.detail.dismissed as string[] | undefined) ?? [];
      const edited = (event.detail.edited as string[] | undefined) ?? [];
      const parts = [
        accepted.length > 0 ? `accepted ${accepted.join(", ")}` : "",
        dismissed.length > 0 ? `kept ${dismissed.join(", ")}` : "",
        edited.length > 0 ? `rewrote ${edited.join(", ")}` : "",
      ].filter(Boolean);
      return `Review answered${parts.length > 0 ? `: ${parts.join("; ")}` : ""}`;
    }
    case "claim_resubmit_requested":
      return "Claim resubmit requested";
    case "signed": {
      const rate = event.detail.edit_rate as number | undefined;
      return rate === undefined
        ? "Note signed"
        : `Note signed (${Math.round(rate * 100)}% of the AI draft changed)`;
    }
    case "claim_created":
      return "Claim created";
    case "claim_submitted":
      return "Claim submitted";
    case "claim_failed":
      return "Claim failed";
    default:
      return event.action;
  }
}

export default function AuditTrail({ events }: { events: AuditEvent[] }) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>Audit trail</CardTitle>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing recorded yet.</p>
        ) : (
          <ol className="space-y-3">
            {events.map((event) => (
              <li key={event.id} className="text-sm">
                <p className="font-medium">{describe(event)}</p>
                <p className="text-xs text-muted-foreground">
                  {formatDateTime(event.created_at)} by {event.actor}
                </p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
