"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { listSessions } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { SessionSummary } from "@/lib/types";
import StatusBadge from "./StatusBadge";

export default function ScheduleList() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listSessions()
      .then((rows) => {
        if (!cancelled) setSessions(rows);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not load sessions. Is the API running on port 8000?");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <Alert variant="destructive" className="mt-6">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!sessions) {
    return (
      <div className="mt-6 space-y-2" aria-label="Loading sessions">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (sessions.length === 0) {
    return <p className="mt-6 text-muted-foreground">No sessions scheduled.</p>;
  }

  return (
    <Card className="mt-6 gap-0 overflow-hidden py-0">
      <ul className="divide-y">
        {sessions.map((s) => (
          <li key={s.id}>
            <Link
              href={`/sessions/${s.id}`}
              className="flex items-center justify-between px-5 py-4 transition-colors hover:bg-accent"
            >
              <div>
                <p className="font-medium">{s.patient_name}</p>
                <p className="text-sm text-muted-foreground">
                  {formatDateTime(s.scheduled_at)}
                </p>
              </div>
              <StatusBadge status={s.note_status} />
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
