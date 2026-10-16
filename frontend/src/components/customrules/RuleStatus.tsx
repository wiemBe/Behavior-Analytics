import { statusLabel, statusReason, statusTone } from "@/components/customrules/authoring";

export function RuleStatus({ status, reason }: { status: string | null; reason: string | null }) {
  const text = statusReason(status, reason);
  return (
    <div className="cr-status">
      <span className={`pill ${statusTone(status)}`}>{statusLabel(status)}</span>
      {text ? <span>{text}</span> : null}
    </div>
  );
}
