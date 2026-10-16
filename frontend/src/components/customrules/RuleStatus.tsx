import { statusLabel, statusReason, statusTone } from "@/components/customrules/authoring";

export function RuleStatus({ status, reason }: { status: string | null; reason: string | null }) {
  return (
    <div className="cr-status">
      <span className={`pill ${statusTone(status)}`}>{statusLabel(status)}</span>
      <span>{statusReason(status, reason)}</span>
    </div>
  );
}
