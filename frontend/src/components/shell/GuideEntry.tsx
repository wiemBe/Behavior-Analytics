"use client";

// The one fixed "Guide" entry at the bottom of the left navigation (GUIDE-3).
//
// The page-header link shows once per account; this entry is how an analyst gets back to the
// guide afterwards, from every page. On a page that has a guide section it goes to that section,
// on any other page to the guide's index. It sits outside the scrolling navigation list, so a
// short viewport scrolls the items above it and never covers one. Text only (D-11).

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { own } from "@/i18n/ownKey";
import { guideForPath } from "@/lib/guideRoutes";
import { rawMessage } from "@/lib/guideText";

export function GuideEntry() {
  const nav = useTranslations("shell.nav");
  const links = useTranslations("guideLinks");
  const pathname = usePathname() ?? "/";
  const section = guideForPath(pathname);
  const title = section
    ? own(rawMessage(links, "titles") as Record<string, string>, section)
    : undefined;
  const href = title !== undefined ? `/guide/${section}` : "/guide";
  return (
    <div className="sidebar-foot">
      <Link
        href={href}
        className="nav-link plain"
        // The index when the page has no section: the visible word is the whole name. With a
        // section the name says which one, and still starts with the visible word.
        aria-label={title !== undefined ? links("named", { topic: title }) : undefined}
        aria-current={pathname === href ? "page" : undefined}
      >
        {nav("guide")}
      </Link>
    </div>
  );
}
