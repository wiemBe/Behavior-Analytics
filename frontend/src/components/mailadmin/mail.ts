// The pure parts of the alert e-mail page: the editor's draft and what makes it differ from what
// is stored, a placeholder inserted at the cursor, the sentence a test-send outcome code maps to,
// and the field a recipient error belongs to. Nothing here reads a catalog or the network.

import { renderRefusal } from "@/i18n/refusalMessage";
import type { Locale } from "@/i18n/locale";
import type { TextTranslator } from "@/i18n/textTranslator";
import type {
  ApiError,
  EmailEffectiveSource,
  EmailTemplate,
  EmailTemplateBody,
  EmailTemplateList,
  EmailTemplateSummary,
} from "@/lib/api";
import { refusalSentences } from "@/lib/refusal";

/** What the three editor fields and the switch hold. The text body is never null in the editor. */
export interface Draft {
  subject: string;
  html: string;
  text: string;
  enabled: boolean;
}

/**
 * A template that has no stored row is shown with what it sends today, so the editor can start
 * from it. Saving it creates a row, and a new row is enabled unless the administrator says
 * otherwise: `enabled` on a key without a row is the API's "meaningless" false, not a choice.
 */
export function draftFrom(template: EmailTemplate): Draft {
  return {
    subject: template.subject,
    html: template.html_body,
    text: template.text_body,
    enabled: template.customised ? template.enabled : true,
  };
}

export function sameDraft(a: Draft, b: Draft): boolean {
  return (
    a.subject === b.subject && a.html === b.html && a.text === b.text && a.enabled === b.enabled
  );
}

/** The request body for a draft. An empty text body is null: the text part is derived from the HTML. */
export function bodyOf(draft: Draft): EmailTemplateBody {
  return {
    subject: draft.subject,
    html_body: draft.html,
    text_body: draft.text.trim() === "" ? null : draft.text,
    enabled: draft.enabled,
  };
}

/** `{{ name }}`: the syntax the API reports for a placeholder. Built here, never typed in markup. */
export function placeholderToken(name: string): string {
  return `{{ ${name} }}`;
}

/** `value` with `text` in place of the selection, and where the caret goes after it. */
export function insertAt(
  value: string,
  start: number,
  end: number,
  text: string,
): { value: string; caret: number } {
  const from = Math.max(0, Math.min(start, value.length));
  const to = Math.max(from, Math.min(end, value.length));
  return { value: value.slice(0, from) + text + value.slice(to), caret: from + text.length };
}

/** What a key sends today, from the list alone (the list rows carry no `effective_source`). */
export function sourceOf(
  row: EmailTemplateSummary,
  list: EmailTemplateList,
): EmailEffectiveSource {
  if (row.customised && row.enabled) return "own";
  if (row.alert_type !== null) {
    const stored = list.templates.find((item) => item.alert_type === null);
    if (stored && stored.customised && stored.enabled) return "default";
  }
  return "shipped";
}

/** The part of an outcome code after `email.test.`, when it is one of the codes the page words. */
export const TEST_OUTCOMES = [
  "sent",
  "not_configured",
  "insecure_login",
  "connection_refused",
  "timeout",
  "connection_failed",
  "tls_failed",
  "auth_failed",
  "recipient_refused",
  "sender_refused",
  "server_error",
] as const;

export type TestOutcomeName = (typeof TEST_OUTCOMES)[number];

const PREFIX = "email.test.";

export function outcomeName(code: string): TestOutcomeName | null {
  if (!code.startsWith(PREFIX)) return null;
  const name = code.slice(PREFIX.length);
  return (TEST_OUTCOMES as readonly string[]).includes(name) ? (name as TestOutcomeName) : null;
}

/**
 * The sentence for an outcome. A code this page knows reads from the catalog; any other code shows
 * the API's own sentence, so a new failure the backend adds is never blank.
 */
export function outcomeSentence(
  outcome: { code: string; message: string },
  say: TextTranslator,
): string {
  const name = outcomeName(outcome.code);
  if (name !== null) {
    const key = `backend.${PREFIX}${name}`;
    if (!say.has || say.has(key)) return say(key);
  }
  return outcome.message.trim() !== "" ? outcome.message : say("mailAdmin.test.unknownOutcome");
}

export interface RecipientFieldErrors {
  address: string[];
  types: string[];
  general: string[];
}

const ADDRESS_CODES = new Set(["email.recipient.address_invalid", "email.recipient.duplicate"]);
const TYPE_CODES = new Set(["email.recipient.alert_type_unknown"]);

/** The reasons a recipient save was refused, each under the field it names. */
export function recipientErrors(
  err: ApiError,
  locale: Locale,
  say: TextTranslator,
  fallback: string[],
): RecipientFieldErrors {
  const out: RecipientFieldErrors = { address: [], types: [], general: [] };
  const sentences = refusalSentences(err);
  if (sentences.length === 0) {
    out.general = fallback;
    return out;
  }
  for (const sentence of sentences) {
    const line = renderRefusal(sentence, locale, say);
    const code = sentence.code ?? "";
    if (ADDRESS_CODES.has(code)) out.address.push(line);
    else if (TYPE_CODES.has(code)) out.types.push(line);
    else out.general.push(line);
  }
  return out;
}
