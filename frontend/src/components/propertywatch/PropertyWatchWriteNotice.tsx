import { useTranslations } from "next-intl";

export function PropertyWatchWriteNotice({ unknown = false }: { unknown?: boolean }) {
  const t = useTranslations("propertyWatch.writeNotice");
  return (
    <div className="notice" role={unknown ? "alert" : undefined}>
      <p>
        <strong>{unknown ? t("permissionsUnread") : t("hidden")}</strong>
      </p>
      <p>{t.rich("body", {
        permission: (chunks) => <code>{chunks}</code>,
        role: (chunks) => <code>{chunks}</code>,
      })}</p>
    </div>
  );
}
