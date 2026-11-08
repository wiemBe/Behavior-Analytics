// What is new or changed since the last visit, and what is waiting on me.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { QueueView } from "@/components/queue/QueueView";
import { PageHeader } from "@/components/ui/PageHeader";
import { CopyLink } from "@/components/listview/CopyLink";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { parseQueueQuery, queueHref } from "@/lib/queue";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("queue");
  return { title: t("meta") };
}

async function QueuePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const t = await getTranslations("queue");
  const query = parseQueueQuery(await searchParams);
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<CopyLink href={queueHref(query)} />} />
      <QueueView query={query} />
    </>
  );
}

export default withRouteMessages("/queue", QueuePage);
