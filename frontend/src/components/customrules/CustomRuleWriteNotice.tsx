import { useTranslations } from "next-intl";

const WRITE_PERMISSION = "custom_rule:write";

export function CustomRuleWriteNotice({ unknown = false }: { unknown?: boolean }) {
  const t = useTranslations("customRules.writeNotice");
  return (
    <div className="notice" role={unknown ? "alert" : undefined}>
      <p>
        <strong>{unknown ? t("unknown") : t("hidden")}</strong>
      </p>
      <p>
        {t("beforePermission")}<code>{WRITE_PERMISSION}</code>.
      </p>
    </div>
  );
}
