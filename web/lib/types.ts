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
  edit_rate: number | null;
  section_edit_rates: Partial<Record<Section, number>>;
  draft_warnings: Finding[];
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

export type FindingKind =
  | "not_documented"
  | "ungrounded"
  | "safety_undocumented"
  | "inconsistent"
  | "other";

export const FINDING_LABELS: Record<FindingKind, string> = {
  not_documented: "Nothing documented",
  ungrounded: "Not supported by the session notes",
  safety_undocumented: "Risk not documented",
  inconsistent: "Does not follow from the other sections",
  other: "Needs a look",
};

export type Finding = {
  section: Section;
  kind: FindingKind;
  message: string;
  source: string;
};

const SOURCE_LABELS: Record<string, string> = {
  grounding: "grounding reviewer",
  safety: "safety reviewer",
  billing: "billing reviewer",
  guardrail: "automatic check",
  general: "reviewer",
};

/** "safety+guardrail" becomes "safety reviewer, automatic check". */
export function sourceLabel(source: string): string {
  return source
    .split("+")
    .map((part) => SOURCE_LABELS[part] ?? part)
    .join(", ");
}

export type ReviewStatus = "paused" | "ready" | "needs_attention";
export type ReviewDecision = "accept" | "dismiss";

export type ReviewResult = {
  status: ReviewStatus;
  round: number;
  // The note text after any accepted fixes. Not saved by the server.
  note: SoapNote;
  findings: Finding[];
  fixes: Partial<Record<Section, string>>;
};

export type ReviewerRow = {
  raised: number;
  shared: number;
  accepted: number;
  dismissed: number;
  rewritten: number;
};

export type AdminOverview = {
  review: {
    reviews_started: number;
    review_rounds: number;
    findings_merged_away: number;
    signed_notes: number;
    signed_without_review: number;
    signed_with_open_findings: number;
    by_reviewer: Record<string, ReviewerRow>;
    by_kind: Record<string, number>;
    recent: {
      note_id: number;
      at: string;
      status: string | null;
      findings: number;
      sources: string[];
    }[];
  };
  edits: {
    signed_notes: number;
    mean_edit_rate: number;
    median_edit_rate: number;
    signed_unchanged_share: number;
    by_section: Record<string, number>;
  };
  claims: Record<ClaimStatusValue, number>;
  llm: {
    purpose: string;
    calls: number;
    errors: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    p50_seconds: number;
    p95_seconds: number;
  }[];
  evals: {
    provider: string;
    model: string;
    date: string;
    summary: {
      catch_specialists: [number, number];
      catch_combined: [number, number];
      false_alarms_specialists: [number, number];
      unusable_replies: number;
      seconds_per_case: number;
      cases: number;
    };
  }[];
  sections: Section[];
};
