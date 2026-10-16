import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  return (
    <BoundaryLoading
      title="Directory"
      what="the directory configuration"
      description="Where directory accounts bind, and a connection test that stores nothing."
    />
  );
}
