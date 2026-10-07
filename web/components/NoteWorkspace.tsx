"use client";

import { CircleCheck, Info, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  createDraft,
  getAudit,
  getNote,
  getSession,
  saveNote,
  signNote,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import {
  SECTIONS,
  type AuditEvent,
  type Note,
  type Section,
  type SessionSummary,
  type SoapNote,
} from "@/lib/types";
import AuditTrail from "./AuditTrail";
import ClaimStatus from "./ClaimStatus";
import DiffView from "./DiffView";
import NoteEditor from "./NoteEditor";
import StatusBadge from "./StatusBadge";

const MIN_INPUT = 20;

type Busy = "draft" | "save" | "sign" | null;
type View = "edit" | "compare";

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}

export default function NoteWorkspace({ sessionId }: { sessionId: number }) {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [edits, setEdits] = useState<SoapNote | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [raw, setRaw] = useState("");
  const [view, setView] = useState<View>("edit");
  const [busy, setBusy] = useState<Busy>(null);
  const [confirmSign, setConfirmSign] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // One key per sign attempt. If the request is retried, the server returns the first result.
  const signKey = useRef<string | null>(null);

  const refreshAudit = useCallback(async () => {
    try {
      setAudit(await getAudit(sessionId));
    } catch {
      setAudit([]);
    }
  }, [sessionId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, n] = await Promise.all([getSession(sessionId), getNote(sessionId)]);
        if (cancelled) return;
        setSession(s);
        setNote(n);
        setEdits(n?.final_note ?? null);
        if (n) setAudit(await getAudit(sessionId));
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const dirty = useMemo(() => {
    if (!note?.final_note || !edits) return false;
    return SECTIONS.some((s) => edits[s] !== note.final_note![s]);
  }, [note, edits]);

  function applyNote(next: Note) {
    setNote(next);
    setEdits(next.final_note);
    setSession((s) => (s ? { ...s, note_status: next.status } : s));
  }

  async function run(kind: Exclude<Busy, null>, action: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const handleDraft = () =>
    run("draft", async () => {
      applyNote(await createDraft(sessionId, raw.trim()));
      await refreshAudit();
    });

  const handleSave = () =>
    run("save", async () => {
      if (!edits) return;
      applyNote(await saveNote(sessionId, edits));
      await refreshAudit();
    });

  const handleSign = async () => {
    await run("sign", async () => {
      applyNote(await signNote(sessionId, signKey.current ?? crypto.randomUUID()));
      await refreshAudit();
    });
    setConfirmSign(false);
  };

  function openSignDialog() {
    signKey.current = crypto.randomUUID();
    setConfirmSign(true);
  }

  function handleEdit(section: Section, text: string) {
    setEdits((prev) => (prev ? { ...prev, [section]: text } : prev));
  }

  if (loading) {
    return (
      <div className="mt-6 space-y-4" aria-label="Loading session">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }

  if (!session) {
    return (
      <Alert variant="destructive" className="mt-6">
        <AlertDescription>{error ?? "Session not found."}</AlertDescription>
      </Alert>
    );
  }

  const signed = note?.status === "signed";

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{session.patient_name}</h1>
          <p className="text-sm text-muted-foreground">
            {formatDateTime(session.scheduled_at)}
          </p>
        </div>
        <StatusBadge status={note?.status ?? null} />
      </div>

      {error && (
        <Alert variant="destructive" className="mt-4 justify-between">
          <AlertDescription>{error}</AlertDescription>
          <Button variant="ghost" size="sm" onClick={() => setError(null)}>
            Dismiss
          </Button>
        </Alert>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <section className="lg:col-span-2">
          {!note || !edits ? (
            <Card>
              <CardHeader>
                <CardTitle>Rough session notes</CardTitle>
                <CardDescription>
                  Paste short, rough notes. Use synthetic information only. The AI drafts a
                  SOAP note for you to review.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Textarea
                  value={raw}
                  onChange={(e) => setRaw(e.target.value)}
                  maxLength={8000}
                  aria-label="Rough session notes"
                  className="min-h-48"
                />
              </CardContent>
              <CardFooter className="justify-between">
                <span className="text-xs text-muted-foreground">
                  {raw.trim().length} characters, at least {MIN_INPUT} needed
                </span>
                <Button
                  onClick={handleDraft}
                  disabled={raw.trim().length < MIN_INPUT || busy !== null}
                >
                  {busy === "draft" && <LoaderCircle className="animate-spin" />}
                  {busy === "draft" ? "Drafting" : "Generate draft"}
                </Button>
              </CardFooter>
            </Card>
          ) : (
            <div className="space-y-4">
              {signed ? (
                <Alert variant="success">
                  <CircleCheck />
                  <AlertDescription>
                    Signed {note.signed_at ? formatDateTime(note.signed_at) : ""}. This note
                    is locked.
                  </AlertDescription>
                </Alert>
              ) : (
                <Alert variant="info">
                  <Info />
                  <AlertDescription>
                    AI generated draft. Review every section and edit as needed before
                    signing.
                  </AlertDescription>
                </Alert>
              )}

              <Tabs value={view} onValueChange={(v) => setView(v as View)}>
                <TabsList>
                  <TabsTrigger value="edit">Edit</TabsTrigger>
                  <TabsTrigger value="compare">Compare with AI draft</TabsTrigger>
                </TabsList>
                <TabsContent value="edit">
                  <NoteEditor
                    value={edits}
                    original={note.ai_draft}
                    readOnly={signed}
                    onChange={handleEdit}
                  />
                </TabsContent>
                <TabsContent value="compare">
                  <DiffView before={note.ai_draft ?? edits} after={edits} />
                </TabsContent>
              </Tabs>

              {!signed && (
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    variant="outline"
                    onClick={handleSave}
                    disabled={!dirty || busy !== null}
                  >
                    {busy === "save" && <LoaderCircle className="animate-spin" />}
                    {busy === "save" ? "Saving" : "Save changes"}
                  </Button>
                  <Button onClick={openSignDialog} disabled={dirty || busy !== null}>
                    Sign note
                  </Button>
                  {dirty && (
                    <span className="text-sm text-warning">
                      Unsaved changes. Save before signing.
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        <aside className="space-y-4">
          {signed && <ClaimStatus sessionId={sessionId} onSettled={refreshAudit} />}
          <AuditTrail events={audit} />
        </aside>
      </div>

      <AlertDialog
        open={confirmSign}
        onOpenChange={(open) => {
          if (busy === null) setConfirmSign(open);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign this note?</AlertDialogTitle>
            <AlertDialogDescription>
              Signing locks the note and starts the claim. You will not be able to edit it
              afterward.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy !== null}
              onClick={(e) => {
                e.preventDefault();
                void handleSign();
              }}
            >
              {busy === "sign" && <LoaderCircle className="animate-spin" />}
              {busy === "sign" ? "Signing" : "Sign note"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
