import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { SearchEditor } from "@/components/searches/SearchEditor";
import { SearchWriteNotice } from "@/components/searches/SearchWriteNotice";
import { searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { api } from "@/lib/api";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("searches.meta");
  return { title: t("new") };
}

export default async function NewSearchPage() {
  const t = await getTranslations("searches.new");
  let access: SearchAccess = "unknown";
  try {
    access = searchAccessFrom(await api.caller());
  } catch {
    access = "unknown";
  }

  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      {access === "writer" ? (
        <SearchEditor mode="create" />
      ) : (
        <>
          <SearchWriteNotice unknown={access === "unknown"} />
          <p className="field-hint">
            <Link href="/searches">{t("back")}</Link>
          </p>
        </>
      )}
    </>
  );
}
