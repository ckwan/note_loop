export const SECTIONS = ["subjective", "objective", "assessment", "plan"] as const;
export type Section = (typeof SECTIONS)[number];

export const SECTION_LABELS: Record<Section, string> = {
  subjective: "Subjective",
  objective: "Objective",
  assessment: "Assessment",
  plan: "Plan",
};

export type SoapNote = Record<Section, string>;

export type NoteStatus = "draft" | "signed";

export type SessionSummary = {
  id: number;
  patient_name: string;
  scheduled_at: string;
  note_status: NoteStatus | null;
};

export type Note = {
  id: number;
  session_id: number;
  status: NoteStatus;
  ai_draft: SoapNote | null;
  final_note: SoapNote | null;
  changed_sections: Section[];
  signed_at: string | null;
};

export type ClaimStatusValue = "pending" | "created" | "submitted" | "failed";

export type Claim = {
  id: number;
  note_id: number;
  status: ClaimStatusValue;
  payer_reference: string | null;
  submitted_at: string | null;
};

export type AuditEvent = {
  id: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
};
