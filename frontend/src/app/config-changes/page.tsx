import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/ui/PageHeader";

// Not in the navigation (decision D-11). The model exists in the backend, but no collector or
// API fills it, so the route says so rather than draw an empty table.
export default function Page() {
  const t = useTranslations("configChanges");
  return (
    <>
      <PageHeader title={t("title")} />
      <div className="notice">
        <p>
          <strong>{t("heading")}</strong>
        </p>
        <p>{t("body")}</p>
      </div>
    </>
  );
}
