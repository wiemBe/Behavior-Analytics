// The rules that decide a log source's class, and the order they apply in.

import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { RulesManager } from "@/components/fleet/RulesManager";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, rateLimitedText, fleetApi, type ClassificationRule } from "@/lib/api";
import { precedenceSteps } from "@/lib/fleet";
import { fleetTextOf, plainCount } from "@/lib/fleetText";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("classification.meta");
  return { title: t("title") };
}

async function ClassificationRulesPage() {
  const t = await getTranslations("classification.page");
  const locale = await getLocale();
  const text = fleetTextOf(await getTranslations(), locale);
  let rules: ClassificationRule[] = [];
  let error: string | null = null;
  try {
    rules = await fleetApi.rules();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, text.say)
        : err instanceof ApiError && err.status === 403
          ? t("forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("expired")
            : t("failed");
  }
  const builtIn = rules.filter((r) => r.built_in).length;

  return (
    <>
      <PageHeader
        guide={guideFor("/admin/classification-rules")}
        title={t("title")}
        description={
          error
            ? t("descriptionError")
            : t("description", {
                total: plainCount(rules.length, text.locale),
                builtIn: plainCount(builtIn, text.locale),
                custom: plainCount(rules.length - builtIn, text.locale),
              })
        }
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errorTitle")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : (
        <>
          <section aria-labelledby="precedence">
            <h2 className="section-title" id="precedence">
              {t("precedenceTitle")}
            </h2>
            <ol className="precedence">
              {precedenceSteps(text).map((step) => (
                <li key={step.key}>
                  <strong>{step.title}</strong>
                  <span className="muted"> — {step.detail}</span>
                </li>
              ))}
            </ol>
            <p className="subtitle">{t("precedenceNote")}</p>
          </section>
          <RulesManager initial={rules} />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/admin/classification-rules", ClassificationRulesPage);
