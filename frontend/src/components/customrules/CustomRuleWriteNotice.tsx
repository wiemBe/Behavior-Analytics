export function CustomRuleWriteNotice({ unknown = false }: { unknown?: boolean }) {
  return (
    <div className="notice" role={unknown ? "alert" : undefined}>
      <p>
        <strong>{unknown ? "Permissions could not be read." : "The rule editor is hidden."}</strong>
      </p>
      <p>
        Creating, editing, enabling and deleting rules needs <code>custom_rule:write</code>. An
        admin or the <code>detection_engineer</code> role can grant it.
      </p>
    </div>
  );
}
