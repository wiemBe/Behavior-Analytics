"use client";

export default function DashboardError() {
  return (
    <p role="alert">
      Dashboard could not be opened. Use Refresh in the top bar; if it still fails, check that the
      backend is reachable.
    </p>
  );
}
