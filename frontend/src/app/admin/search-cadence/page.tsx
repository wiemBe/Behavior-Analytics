// How often each search family queries QRadar, and what that costs in
// detection delay. One table, one row per family, in the API's order.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { SearchCadenceTable } from "@/components/cadence/SearchCadenceTable";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type SearchCadenceItem } from "@/lib/api";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("cadence.meta");
  return { title: t("title") };
}

export default async function SearchCadencePage() {
  const t = await getTranslations("cadence.page");
  const errors = await getTranslations("errors");
  let items: SearchCadenceItem[] = [];
  let error: string | null = null;
  try {
    items = await api.searchCadence();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? t("forbidden")
        : err instanceof ApiError && err.status === 401
          ? errors("sessionExpired")
          : t("failed");
  }

  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("couldNotShow")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("emptyTitle")}</strong>
          </p>
          <p>{t("empty")}</p>
        </div>
      ) : (
        <SearchCadenceTable items={items} />
      )}
    </>
  );
}
