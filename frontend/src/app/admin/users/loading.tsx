import { BoundaryLoading } from "@/components/boundary/BoundaryLoading";

export default function Loading() {
  return (
    <BoundaryLoading
      title="Users"
      what="users"
      description="Accounts that can sign in, and the roles an administrator assigned. Last login is UTC."
    />
  );
}
