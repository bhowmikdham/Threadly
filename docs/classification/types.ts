// Handoff only; runtime OpenAPI/Pydantic schemas are authoritative.
export type Category = "finance_payments" | "hr" | "it" | "legal_contracts"
  | "meeting_scheduling" | "other" | "projects";
export type Priority = "High" | "Medium" | "Low";
export type Action = "approve" | "attend" | "complete_submit" | "edit"
  | "no_action" | "reply" | "review";
export type Labels = {
  needs_reply: boolean;
  priority: Priority;
  category: Category;
  action: Action;
};
export type Evidence = Record<keyof Labels, string[]>;
export type ClassificationRequest = { time_zone?: string };
type Metadata = {
  schema_version: "email-classification-with-action.v1";
  thread_id: string;
  source_message_ids: string[];
  source_fingerprint: string;
  reason_codes: string[];
  evaluated_at: string;
  valid_until: string;
  time_zone: string;
  release_id: string;
  source: "live_gmail";
  coverage: "cleaned_text_only";
  persisted: false;
};
export type ClassificationResponse = Metadata & (
  | { status: "classified"; labels: Labels; evidence: Evidence }
  | { status: "needs_review" | "skipped"; labels: null; evidence: null }
);
export const categoryDisplay: Record<Category, string> = {
  finance_payments: "Finance", hr: "HR", it: "IT", legal_contracts: "Legal",
  meeting_scheduling: "Meeting", other: "Other", projects: "Projects",
};
export const actionDisplay: Record<Action, string> = {
  approve: "Approve", attend: "Attend", complete_submit: "Complete/Submit",
  edit: "Edit", no_action: "No Action", reply: "Reply", review: "Review",
};
