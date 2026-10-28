// The Guide's contents: every section under its group, each with the question it answers. The
// text is rendered on the server (`getTranslations`) and never reaches the browser.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { PageHeader } from "@/components/ui/PageHeader";
import { GUIDE_GROUPS, GUIDE_SECTIONS } from "@/lib/guideSections";
import { rawMessage, readGuideSection } from "@/lib/guideText";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("guide");
  return { title: t("title") };
}

export default async function GuidePage() {
  const t = await getTranslations("guide");
  const sections = rawMessage(t, "sections");
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} />
      {GUIDE_GROUPS.map((group) => (
        <section key={group} aria-labelledby={`guide-group-${group}`} className="guide-group">
          <h2 id={`guide-group-${group}`}>{t(`groups.${group}`)}</h2>
          <ul className="guide-index">
            {GUIDE_SECTIONS.filter((section) => section.group === group).map((section) => {
              const text = readGuideSection(sections, section.id);
              return (
                <li key={section.id}>
                  <Link href={`/guide/${section.id}`} className="guide-entry">
                    {text.title}
                  </Link>
                  <p className="guide-entry-answers">{text.answers}</p>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}
