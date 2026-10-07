import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SECTIONS, SECTION_LABELS, type Section, type SoapNote } from "@/lib/types";

type Props = {
  value: SoapNote;
  original: SoapNote | null;
  readOnly: boolean;
  onChange: (section: Section, text: string) => void;
};

export default function NoteEditor({ value, original, readOnly, onChange }: Props) {
  return (
    <div className="space-y-4">
      {SECTIONS.map((section) => {
        const edited = original !== null && value[section] !== original[section];
        return (
          <Card key={section} className="gap-3">
            <CardHeader className="flex-row items-center justify-between">
              <Label htmlFor={`field-${section}`} className="text-base">
                {SECTION_LABELS[section]}
              </Label>
              {edited && <Badge variant="warning">Edited</Badge>}
            </CardHeader>
            <CardContent>
              <Textarea
                id={`field-${section}`}
                value={value[section]}
                readOnly={readOnly}
                onChange={(e) => onChange(section, e.target.value)}
              />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
