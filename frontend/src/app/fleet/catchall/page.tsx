// The catch-all senders view.
//
// The question: which unconfigured devices are sending to the catch-all
// sources, and how much? The queue answers it first; the per-source buckets
// beneath show the volume and what was and was not measured.

import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { fleetTextOf, type FleetText } from "@/lib/fleetText";
import {
  ApiError,
  rateLimitedText,
  catchallApi,
  type CatchallSender,
  type CatchallSourceList,
  type Page,
} from "@/lib/api";
import { withRouteMessages } from "@/i18n/RouteMessages";

import { SendersQueue } from "./SendersQueue";
import { SourceVolume } from "./SourceVolume";

import "./catchall.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("catchall.meta");
  return { title: t("title") };
}

// Rendered per request: the queue is live data.
export const dynamic = "force-dynamic";

function failureText(err: unknown, text: FleetText): string {
  if (err instanceof ApiError && err.status === 429) return rateLimitedText(err, text.say);
  if (err instanceof ApiError && err.status === 403) return text.say("fleet.errors.forbidden");
  if (err instanceof ApiError && err.status === 401) return text.say("errors.sessionExpired");
  return text.say("catchall.page.unreachable");
}

async function CatchallPage() {
  const t = await getTranslations("catchall.page");
  const text = fleetTextOf(await getTranslations(), await getLocale());
  let sources: CatchallSourceList | null = null;
  let initial: Page<CatchallSender> | null = null;
  let error: string | null = null;
  const now = new Date().toISOString();
  try {
    sources = await catchallApi.sources();
    if (sources.items.length > 0) {
      initial = await catchallApi.senders({ review_status: "NEW", limit: 50 });
    }
  } catch (err) {
    error = failureText(err, text);
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/fleet/catchall")}
        title={t("title")}
        description={t("description")}
      />
      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("noticeHeading")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources === null || sources.items.length === 0 || initial === null ? (
        <div className="notice">
          <p>
            <strong>{t("emptyHeading")}</strong>
          </p>
          <p>
            {t("emptyBody", {
              types:
                sources && sources.catchall_type_ids.length > 0
                  ? sources.catchall_type_ids.join(", ")
                  : t("configuredTypes"),
            })}
          </p>
        </div>
      ) : (
        <>
          <SendersQueue sources={sources} initial={initial} />
          <SourceVolume sources={sources} now={now} />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/fleet/catchall", CatchallPage);
