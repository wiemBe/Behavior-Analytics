import { PageHeader } from "@/components/ui/PageHeader";

// Not in the navigation (decision D-11). The model exists in the backend, but no collector or
// API fills it, so the route says so rather than draw an empty table.
export default function Page() {
  return (
    <>
      <PageHeader title="Configuration changes" />
      <div className="notice">
        <p>
          <strong>Configuration changes are not collected.</strong>
        </p>
        <p>
          A data model for them exists, but no collector or API fills it, so this page has
          nothing to show.
        </p>
      </div>
    </>
  );
}
