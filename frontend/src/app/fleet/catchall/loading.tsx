import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  return (
    <BoundaryLoading
      title="Catch-all senders"
      what="the catch-all senders"
      description="A sender is the syslog hostname QRadar recorded (logsourceidentifier) for events that landed in a catch-all source. It is often a relay, and it is a candidate unconfigured device, not a verdict. Times are UTC."
      offset="small"
    />
  );
}
