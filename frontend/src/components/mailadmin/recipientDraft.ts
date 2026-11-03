// The recipient form's draft. It lives above the tabs, like the template drafts, so a half-typed
// recipient survives a visit to the other tab, and the leave guard can count it.

import type { EmailRecipientBody } from "@/lib/api";

export interface FormState {
  /** Null while adding. */
  id: string | null;
  address: string;
  minSeverity: EmailRecipientBody["min_severity"];
  everyType: boolean;
  types: string[];
  enabled: boolean;
}

export const BLANK: FormState = {
  id: null,
  address: "",
  minSeverity: "LOW",
  everyType: true,
  types: [],
  enabled: true,
};

/** The open form and what it was opened with, so "changed" means changed. */
export interface RecipientDraft {
  form: FormState;
  base: FormState;
}

export function isRecipientDirty(draft: RecipientDraft | null): boolean {
  if (draft === null) return false;
  const { form, base } = draft;
  return (
    form.address !== base.address ||
    form.minSeverity !== base.minSeverity ||
    form.everyType !== base.everyType ||
    form.enabled !== base.enabled ||
    form.types.length !== base.types.length ||
    form.types.some((type) => !base.types.includes(type))
  );
}
