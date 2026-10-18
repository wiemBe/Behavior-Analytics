export function PropertyWatchWriteNotice({ unknown = false }: { unknown?: boolean }) {
  return (
    <div className="notice" role={unknown ? "alert" : undefined}>
      <p>
        <strong>{unknown ? "Permissions could not be read." : "The property-watch editor is hidden."}</strong>
      </p>
      <p>
        Creating, editing, enabling and deleting property watches needs{" "}
        <code>property_watch:write</code>. An admin or the <code>detection_engineer</code> role can
        grant it.
      </p>
    </div>
  );
}
