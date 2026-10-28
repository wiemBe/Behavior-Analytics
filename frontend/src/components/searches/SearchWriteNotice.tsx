// Shown where the editor would be, when this caller cannot write a search.

import { useTranslations } from "next-intl";

import "@/components/ui/controls.css";

export function SearchWriteNotice({ unknown }: { unknown: boolean }) {
  const t = useTranslations("searches.notice");
  return (
    <div className="banner" role="status">
      <p>
        {unknown ? t("hiddenUnknown") : t("hidden")}
        {t.rich("needs", {
          perm: (chunks) => <code className="perm">{chunks}</code>,
          code: (chunks) => <code>{chunks}</code>,
        })}
      </p>
    </div>
  );
}
