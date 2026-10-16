// Shown where the editor would be, when this caller cannot write a search.

import "@/components/ui/controls.css";

export function SearchWriteNotice({ unknown }: { unknown: boolean }) {
  return (
    <div className="banner" role="status">
      <p>
        {unknown
          ? "Your permissions could not be read, so the editor stays hidden. "
          : "The editor is hidden. "}
        Changing a stored search needs <code className="perm">search:write</code>. An admin or the{" "}
        <code>detection_engineer</code> role can grant it. Stored searches stay readable.
      </p>
    </div>
  );
}
