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
  answerReview,
  createDraft,
  getAudit,
  getNote,
  getReview,
  getSession,
  saveNote,
  signNote,
  startReview,
  suggestWording,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import {
  SECTIONS,
  type AuditEvent,
  type Note,
  type ReviewDecision,
  type ReviewResult,
  type Section,
  type SessionSummary,
  type SoapNote,
} from "@/lib/types";
import AuditTrail from "./AuditTrail";
import ClaimStatus from "./ClaimStatus";
import DiffView from "./DiffView";
import NoteEditor from "./NoteEditor";
import ReviewPanel from "./ReviewPanel";
import StatusBadge from "./StatusBadge";

const MIN_INPUT = 20;

type Busy = "draft" | "save" | "sign" | "suggest" | "review" | null;
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
  const [notice, setNotice] = useState<string | null>(null);
  // Wording suggestions waiting for the therapist to accept or dismiss them.
  const [suggestions, setSuggestions] = useState<Partial<Record<Section, string>>>({});
  const [suggestTarget, setSuggestTarget] = useState<Section | "all" | null>(null);
  // Sections whose text came from an accepted suggestion and has not been saved yet.
  const [aiAssisted, setAiAssisted] = useState<Section[]>([]);
  // The review before signing. It pauses until the therapist answers.
  const [review, setReview] = useState<ReviewResult | null>(null);
  // The text each section had when the current round began, to spot what was rewritten.
  const [reviewBase, setReviewBase] = useState<SoapNote | null>(null);
  const [decisions, setDecisions] = useState<Partial<Record<Section, ReviewDecision>>>({});
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
        if (n && n.status !== "signed") {
          // Bring back a review that was waiting for an answer, such as after a reload.
          // Only if it checked the text now on screen. A review run on unsaved text
          // was about text that is gone, so showing it would mislead.
          const r = await getReview(sessionId).catch(() => null);
          const sameText =
            r !== null && n.final_note !== null && SECTIONS.every((x) => r.note[x] === n.final_note![x]);
          if (!cancelled && r?.status === "paused" && sameText) {
            setReview(r);
            setReviewBase(r.note);
          }
        }
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
      applyNote(await saveNote(sessionId, edits, aiAssisted));
      setAiAssisted([]);
      await refreshAudit();
    });

  const handleSuggest = (target: Section | "all") =>
    run("suggest", async () => {
      if (!edits) return;
      setNotice(null);
      setSuggestTarget(target);
      try {
        const sections: Section[] = target === "all" ? [...SECTIONS] : [target];
        const res = await suggestWording(sessionId, edits, sections);

        const next = { ...suggestions };
        let changed = 0;
        for (const section of sections) {
          const text = res.suggestions[section]?.trim();
          if (text && text !== edits[section].trim()) {
            next[section] = text;
            changed += 1;
          } else {
            delete next[section];
          }
        }
        setSuggestions(next);
        if (changed === 0) {
          setNotice("No wording changes suggested. The text already reads formally.");
        }
        await refreshAudit();
      } finally {
        setSuggestTarget(null);
      }
    });

  function clearSuggestion(section: Section) {
    setSuggestions((prev) => {
      const next = { ...prev };
      delete next[section];
      return next;
    });
  }

  function acceptSuggestion(section: Section) {
    const text = suggestions[section];
    if (text === undefined) return;
    setEdits((prev) => (prev ? { ...prev, [section]: text } : prev));
    clearSuggestion(section);
    setAiAssisted((prev) => (prev.includes(section) ? prev : [...prev, section]));
  }

  const handleStartReview = () =>
    run("review", async () => {
      if (!edits) return;
      setNotice(null);
      const result = await startReview(sessionId, edits);
      setReview(result);
      setReviewBase(result.note);
      setDecisions({});
      await refreshAudit();
    });

  function handleDecide(section: Section, decision: ReviewDecision) {
    setDecisions((prev) => ({ ...prev, [section]: decision }));
  }

  const handleContinueReview = () =>
    run("review", async () => {
      if (!edits || !review || !reviewBase) return;

      // Text typed since the round began. The server checks what is on screen, so any
      // section that differs is sent as an edit, and a typed edit beats a proposed fix.
      const typed: Partial<Record<Section, string>> = {};
      for (const s of SECTIONS) {
        if (edits[s].trim() !== "" && edits[s] !== reviewBase[s]) typed[s] = edits[s];
      }
      const answers: Partial<Record<Section, ReviewDecision>> = {};
      for (const f of review.findings) {
        const d = decisions[f.section];
        if (d && typed[f.section] === undefined) answers[f.section] = d;
      }

      const result = await answerReview(sessionId, answers, typed);

      // Put accepted fixes into the editor and mark them AI assisted. The review itself
      // saves nothing. The therapist saves, as with any other edit.
      const accepted = SECTIONS.filter(
        (s) => answers[s] === "accept" && review.fixes[s] !== undefined,
      );
      if (accepted.length > 0) {
        setEdits((prev) => {
          if (!prev) return prev;
          const next = { ...prev };
          for (const s of accepted) next[s] = result.note[s];
          return next;
        });
        setAiAssisted((prev) => [...prev, ...accepted.filter((s) => !prev.includes(s))]);
        accepted.forEach(clearSuggestion);
      }

      setReview(result);
      setReviewBase(result.note);
      setDecisions({});
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
    // A suggestion was written for the old text, so it no longer applies.
    clearSuggestion(section);
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
                    {note.edit_rate !== null &&
                      (note.edit_rate === 0
                        ? " Signed as the AI wrote it."
                        : ` You changed ${Math.round(note.edit_rate * 100)}% of the AI draft.`)}
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

              {notice && !signed && (
                <Alert variant="info">
                  <Info />
                  <AlertDescription>{notice}</AlertDescription>
                </Alert>
              )}

              {!signed && note && note.draft_warnings.length > 0 && (
                <Alert variant="warning">
                  <Info />
                  <AlertDescription>
                    <p className="font-medium">
                      Automatic checks flagged the AI draft. Verify these before signing.
                    </p>
                    <ul className="mt-1 list-disc pl-5">
                      {note.draft_warnings.map((w) => (
                        <li key={`${w.section}-${w.kind}`}>
                          <span className="capitalize">{w.section}</span>: {w.message}
                        </li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}

              {!signed && (
                <ReviewPanel
                  review={review}
                  base={reviewBase}
                  current={edits}
                  decisions={decisions}
                  busy={busy !== null}
                  running={busy === "review"}
                  onStart={handleStartReview}
                  onDecide={handleDecide}
                  onContinue={handleContinueReview}
                />
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
                    suggestions={suggestions}
                    suggestTarget={suggestTarget}
                    busy={busy !== null}
                    onSuggest={handleSuggest}
                    onAccept={acceptSuggestion}
                    onDismiss={clearSuggestion}
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
