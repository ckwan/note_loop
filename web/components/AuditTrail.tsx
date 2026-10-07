import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { AuditEvent } from "@/lib/types";

function describe(event: AuditEvent): string {
  switch (event.action) {
    case "draft_generated":
      return "AI draft generated";
    case "edited": {
      const sections = (event.detail.sections as string[] | undefined) ?? [];
      return `Edited ${sections.join(", ")}`;
    }
    case "signed":
      return "Note signed";
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
