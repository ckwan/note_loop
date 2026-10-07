import { Badge } from "@/components/ui/badge";
import type { NoteStatus } from "@/lib/types";

const STYLES = {
  none: { label: "No note", variant: "secondary" },
  draft: { label: "Draft", variant: "info" },
  signed: { label: "Signed", variant: "success" },
} as const;

export default function StatusBadge({ status }: { status: NoteStatus | null }) {
  const { label, variant } = STYLES[status ?? "none"];
  return <Badge variant={variant}>{label}</Badge>;
}
