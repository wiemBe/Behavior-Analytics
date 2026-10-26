import { statusLabel, statusReason, statusTone } from "@/components/customrules/authoring";
import { useTranslations } from "next-intl";
import { asTextTranslator } from "@/i18n/textTranslator";

export function RuleStatus({ status, reason }: { status: string | null; reason: string | null }) {
  const say = asTextTranslator(useTranslations());
  const text = statusReason(status, reason);
  return (
    <div className="cr-status">
      <span className={`pill ${statusTone(status)}`}>{statusLabel(status, say)}</span>
      {text ? <span>{text}</span> : null}
    </div>
  );
}
