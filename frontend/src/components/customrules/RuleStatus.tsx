import { statusLabel, statusReason, statusTone } from "@/components/customrules/authoring";
import { useTranslations } from "next-intl";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useBackendMessage } from "@/i18n/backendMessage";

export function RuleStatus({
  status,
  reason,
  reasonCode,
  reasonParams,
}: {
  status: string | null;
  reason: string | null;
  reasonCode?: string | null;
  reasonParams?: Record<string, unknown> | null;
}) {
  const say = asTextTranslator(useTranslations());
  const backendMessage = useBackendMessage();
  const fallback = statusReason(status, reason);
  const text = fallback
    ? backendMessage({ text: fallback, code: reasonCode, params: reasonParams })
    : null;
  return (
    <div className="cr-status">
      <span className={`pill ${statusTone(status)}`}>{statusLabel(status, say)}</span>
      {text ? <span>{text}</span> : null}
    </div>
  );
}
