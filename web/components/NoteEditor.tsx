import { LoaderCircle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SECTIONS, SECTION_LABELS, type Section, type SoapNote } from "@/lib/types";
import { InlineDiff } from "./DiffView";

type Props = {
  value: SoapNote;
  original: SoapNote | null;
  readOnly: boolean;
  onChange: (section: Section, text: string) => void;
  suggestions: Partial<Record<Section, string>>;
  // Which suggestion request is running right now, if any.
  suggestTarget: Section | "all" | null;
  // True while any request is running, so the buttons cannot overlap.
  busy: boolean;
  onSuggest: (target: Section | "all") => void;
  onAccept: (section: Section) => void;
  onDismiss: (section: Section) => void;
};

export default function NoteEditor({
  value,
  original,
  readOnly,
  onChange,
  suggestions,
  suggestTarget,
  busy,
  onSuggest,
  onAccept,
  onDismiss,
}: Props) {
  return (
    <div className="space-y-4">
      {!readOnly && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">
            Edited a section down to short phrases? Ask the AI for more formal wording, then
            accept or dismiss each suggestion.
          </p>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => onSuggest("all")}>
            {suggestTarget === "all" ? <LoaderCircle className="animate-spin" /> : <Sparkles />}
            {suggestTarget === "all" ? "Suggesting" : "Suggest wording for all sections"}
          </Button>
        </div>
      )}

      {SECTIONS.map((section) => {
        const edited = original !== null && value[section] !== original[section];
        const suggestion = suggestions[section];
        return (
          <Card key={section} className="gap-3">
            <CardHeader className="flex-row items-center justify-between">
              <Label htmlFor={`field-${section}`} className="text-base">
                {SECTION_LABELS[section]}
              </Label>
              <div className="flex items-center gap-2">
                {edited && <Badge variant="warning">Edited</Badge>}
                {!readOnly && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => onSuggest(section)}
                    aria-label={`Suggest wording for ${SECTION_LABELS[section]}`}
                  >
                    {suggestTarget === section ? (
                      <LoaderCircle className="animate-spin" />
                    ) : (
                      <Sparkles />
                    )}
                    {suggestTarget === section ? "Suggesting" : "Suggest wording"}
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                id={`field-${section}`}
                value={value[section]}
                readOnly={readOnly}
                onChange={(e) => onChange(section, e.target.value)}
              />
              {suggestion !== undefined && !readOnly && (
                <div
                  role="region"
                  aria-label={`AI suggestion for ${SECTION_LABELS[section]}`}
                  className="space-y-3 rounded-lg border border-info/30 bg-info/5 p-3"
                >
                  <div className="flex items-center gap-2 text-sm font-medium text-info">
                    <Sparkles className="size-4" />
                    AI suggestion
                  </div>
                  <InlineDiff before={value[section]} after={suggestion} />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => onAccept(section)}>
                      Accept
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => onDismiss(section)}>
                      Dismiss
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
