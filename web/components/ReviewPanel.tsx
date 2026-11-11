import { CircleCheck, Info, LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  FINDING_LABELS, sourceLabel,
  SECTION_LABELS,
  SECTIONS,
  type Finding,
  type ReviewDecision,
  type ReviewResult,
  type Section,
  type SoapNote,
} from "@/lib/types";
import { InlineDiff } from "./DiffView";

type Props = {
  review: ReviewResult | null;
  // The text each section had when this round of the review began.
  base: SoapNote | null;
  // The text on screen now, which the therapist may have typed into.
  current: SoapNote;
  decisions: Partial<Record<Section, ReviewDecision>>;
  busy: boolean;
  running: boolean;
  onStart: () => void;
  onDecide: (section: Section, decision: ReviewDecision) => void;
  onContinue: () => void;
};

function groupBySection(findings: Finding[]): [Section, Finding[]][] {
  return SECTIONS.map((s) => [s, findings.filter((f) => f.section === s)] as [Section, Finding[]]).filter(
    ([, list]) => list.length > 0,
  );
}

export default function ReviewPanel({
  review,
  base,
  current,
  decisions,
  busy,
  running,
  onStart,
  onDecide,
  onContinue,
}: Props) {
  const paused = review?.status === "paused";
  const groups = review ? groupBySection(review.findings) : [];

  // A section counts as answered if the therapist chose, or typed new text for it.
  const answered = (section: Section) =>
    decisions[section] !== undefined ||
    (base !== null && current[section].trim() !== "" && current[section] !== base[section]);
  const allAnswered = groups.every(([section]) => answered(section));

  return (
    <Card className="gap-3" aria-label="Review before signing">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="size-5" />
            Review before signing
          </CardTitle>
          {!paused && (
            <Button variant="outline" size="sm" disabled={busy} onClick={onStart}>
              {running && <LoaderCircle className="animate-spin" />}
              {running ? "Reviewing" : review ? "Run review again" : "Review note"}
            </Button>
          )}
        </div>
        <CardDescription>
          Checks the note against your rough session notes for empty sections, facts the notes do not
          support, and risk language that was not documented. It never changes the note by itself.
          Reviewing is optional.
        </CardDescription>
      </CardHeader>

      {review?.status === "ready" && (
        <CardContent>
          <Alert variant="success">
            <CircleCheck />
            <AlertDescription>
              No open problems. If you accepted fixes, they are in the editor. Save your changes,
              then sign.
            </AlertDescription>
          </Alert>
        </CardContent>
      )}

      {review?.status === "needs_attention" && (
        <CardContent className="space-y-3">
          <Alert variant="warning">
            <TriangleAlert />
            <AlertDescription>
              The review used all its rounds and some problems are still open. You can fix them in
              the editor and run the review again, or sign if you are satisfied with the note.
            </AlertDescription>
          </Alert>
          <ul className="space-y-1 text-sm">
            {review.findings.map((f) => (
              <li key={`${f.section}-${f.kind}`}>
                <span className="font-medium">{SECTION_LABELS[f.section]}:</span>{" "}
                {FINDING_LABELS[f.kind]}. {f.message}
              </li>
            ))}
          </ul>
        </CardContent>
      )}

      {paused && review && base && (
        <>
          <CardContent className="space-y-4">
            <Alert variant="info">
              <Info />
              <AlertDescription>
                {review.findings.length} problem{review.findings.length === 1 ? "" : "s"} found.
                For each section, use the proposed fix, keep your text, or rewrite it in the editor
                below. Then continue.
              </AlertDescription>
            </Alert>

            {groups.map(([section, list]) => {
              const fix = review.fixes[section];
              const decision = decisions[section];
              const rewritten = current[section] !== base[section] && current[section].trim() !== "";
              return (
                <section
                  key={section}
                  aria-label={`Review of ${SECTION_LABELS[section]}`}
                  className="space-y-3 rounded-lg border p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-medium">{SECTION_LABELS[section]}</h3>
                    {list.map((f) => (
                      <Badge
                        key={f.kind}
                        variant={f.kind === "safety_undocumented" ? "destructive" : "warning"}
                      >
                        {FINDING_LABELS[f.kind]}
                      </Badge>
                    ))}
                  </div>
                  <ul className="space-y-1 text-sm">
                    {list.map((f) => (
                      <li key={f.kind}>
                        {f.message}{" "}
                        <span className="text-xs text-muted-foreground">
                          Raised by the {sourceLabel(f.source)}.
                        </span>
                      </li>
                    ))}
                  </ul>

                  {fix !== undefined ? (
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">Proposed fix</p>
                      <InlineDiff before={base[section]} after={fix} />
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No automatic fix. This needs information only you have. Write it in the editor,
                      or keep the section as written.
                    </p>
                  )}

                  {rewritten ? (
                    <p className="text-sm text-info">
                      You rewrote this section. Your text will be checked when you continue.
                    </p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {fix !== undefined && (
                        <Button
                          size="sm"
                          variant={decision === "accept" ? "default" : "outline"}
                          aria-pressed={decision === "accept"}
                          onClick={() => onDecide(section, "accept")}
                        >
                          Use fix
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant={decision === "dismiss" ? "default" : "outline"}
                        aria-pressed={decision === "dismiss"}
                        onClick={() => onDecide(section, "dismiss")}
                      >
                        Keep as written
                      </Button>
                    </div>
                  )}
                </section>
              );
            })}
          </CardContent>
          <CardFooter className="justify-between gap-3">
            <span className="text-xs text-muted-foreground">Round {review.round + 1}</span>
            <Button onClick={onContinue} disabled={!allAnswered || busy}>
              {running && <LoaderCircle className="animate-spin" />}
              {running ? "Checking" : "Continue review"}
            </Button>
          </CardFooter>
        </>
      )}
    </Card>
  );
}
