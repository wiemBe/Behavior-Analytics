// One guide section: the question it answers (the page description), its text, the numbered
// steps of a task, the states the analyst may meet, and the sections it points to. Rendered on the
// server only; an id the guide does not have is a 404.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";

import { Blocks, Markup } from "@/components/guide/Markup";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideSection } from "@/lib/guideSections";
import { rawMessage, readGuideSection } from "@/lib/guideText";
import { withRouteMessages } from "@/i18n/RouteMessages";

type Params = Promise<{ section: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { section: id } = await params;
  if (!guideSection(id)) return {};
  const t = await getTranslations("guide");
  return { title: readGuideSection(rawMessage(t, "sections"), id).title };
}

async function GuideSectionPage({ params }: { params: Params }) {
  const { section: id } = await params;
  const facts = guideSection(id);
  if (!facts) notFound();
  const t = await getTranslations("guide");
  const sections = rawMessage(t, "sections");
  const text = readGuideSection(sections, id);
  return (
    <>
      <PageHeader
        title={text.title}
        description={text.answers}
        actions={<Link href="/guide">{t("ui.allTopics")}</Link>}
      />
      <article className="guide-article">
        <dl className="guide-facts">
          {facts.routes.length > 0 && (
            <div>
              <dt>{t("ui.routes", { count: facts.routes.length })}</dt>
              <dd>
                <ul className="guide-inline">
                  {facts.routes.map((route) => (
                    <li key={route}>
                      {route.includes("[") ? (
                        <code>{route}</code>
                      ) : (
                        <Link href={route}>
                          <code>{route}</code>
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
          <div>
            <dt>{t("ui.audience")}</dt>
            <dd>{t(`ui.audiences.${facts.audience}`)}</dd>
          </div>
          <div>
            <dt>{t("ui.permission")}</dt>
            <dd>
              {text.permission !== undefined ? (
                <Markup text={text.permission} />
              ) : facts.permissions.length === 0 ? (
                t("ui.noPermission")
              ) : (
                <ul className="guide-inline">
                  {facts.permissions.map((code) => (
                    <li key={code}>
                      <code>{code}</code>
                    </li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
        </dl>
        <div className="guide-text">
          <Blocks blocks={text.body} />
        </div>
        {text.steps && (
          <section aria-labelledby="guide-steps">
            <h2 id="guide-steps">{t("ui.steps")}</h2>
            <ol className="guide-steps">
              {text.steps.map((step, index) => (
                <li key={index}>
                  <Markup text={step} />
                </li>
              ))}
            </ol>
            {text.afterSteps && (
              <div className="guide-text">
                <Blocks blocks={text.afterSteps} />
              </div>
            )}
          </section>
        )}
        {text.states && (
          <section aria-labelledby="guide-states">
            <h2 id="guide-states">{t("ui.states")}</h2>
            <div className="guide-text">
              <Blocks blocks={text.states} />
            </div>
          </section>
        )}
        {facts.related.length > 0 && (
          <section aria-labelledby="guide-related">
            <h2 id="guide-related">{t("ui.related")}</h2>
            <ul className="guide-related">
              {facts.related.map((related) => (
                <li key={related}>
                  <Link href={`/guide/${related}`}>{readGuideSection(sections, related).title}</Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </article>
    </>
  );
}

export default withRouteMessages("/guide/[section]", GuideSectionPage);
