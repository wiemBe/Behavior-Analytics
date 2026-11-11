// What the last shift left: the latest handover record, and the form that writes the next one.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ComposeView } from "@/components/handover/ComposeView";
import { HandoverView } from "@/components/handover/HandoverView";
import { WriteLink } from "@/components/handover/WriteLink";
import { PageHeader } from "@/components/ui/PageHeader";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { guideFor } from "@/lib/guideRoutes";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("handover");
  return { title: t("meta") };
}

const first = (value: string | string[] | undefined): string | undefined =>
  Array.isArray(value) ? value[0] : value;

async function HandoverPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("handover");
  const params = await searchParams;
  const compose = first(params.compose) === "1";
  const id = first(params.id)?.trim() || null;
  return (
    <>
      <PageHeader
        title={compose ? t("compose.title") : t("title")}
        description={compose ? t("compose.description") : t("description")}
        actions={compose ? undefined : <WriteLink />}
        guide={guideFor("/handover")}
      />
      {compose ? <ComposeView /> : <HandoverView id={id} />}
    </>
  );
}

export default withRouteMessages("/handover", HandoverPage);
