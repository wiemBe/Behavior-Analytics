import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";
import { WindowSwitch } from "@/components/fleet/WindowSwitch";

export default function Loading() {
  return (
    <BoundaryLoading
      title="Log-source fleet"
      what="the log-source fleet"
      description="Volume per class of log source against its baseline, and the sources waiting for review. Times are UTC."
      actions={<WindowSwitch active="24h" />}
    />
  );
}
