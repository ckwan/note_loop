import { diffWords } from "diff";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SECTIONS, SECTION_LABELS, type SoapNote } from "@/lib/types";

function InlineDiff({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after);
  return (
    <p className="text-sm leading-6 whitespace-pre-wrap">
      {parts.map((part, i) => {
        if (part.added) {
          return (
            <ins key={i} className="bg-success/10 text-success no-underline">
              {part.value}
            </ins>
          );
        }
        if (part.removed) {
          return (
            <del key={i} className="bg-destructive/10 text-destructive">
              {part.value}
            </del>
          );
        }
        return <span key={i}>{part.value}</span>;
      })}
    </p>
  );
}

export default function DiffView({ before, after }: { before: SoapNote; after: SoapNote }) {
  const editedCount = SECTIONS.filter((s) => before[s] !== after[s]).length;

  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        {editedCount === 0
          ? "No edits yet. The note matches the AI draft."
          : `${editedCount} of ${SECTIONS.length} sections edited.`}{" "}
        <del className="bg-destructive/10 px-1 text-destructive">Removed from the AI draft</del>{" "}
        <ins className="bg-success/10 px-1 text-success no-underline">Added by you</ins>
      </p>
      <div className="space-y-4">
        {SECTIONS.map((section) => {
          const changed = before[section] !== after[section];
          return (
            <Card key={section} className="gap-3">
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">{SECTION_LABELS[section]}</CardTitle>
                <span className="text-xs text-muted-foreground">
                  {changed ? "Edited" : "Unchanged"}
                </span>
              </CardHeader>
              <CardContent>
                {changed ? (
                  <InlineDiff before={before[section]} after={after[section]} />
                ) : (
                  <p className="text-sm leading-6 whitespace-pre-wrap text-muted-foreground">
                    {after[section]}
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
