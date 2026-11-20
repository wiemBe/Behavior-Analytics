// "Administration → Features" inside a notice that says a family is off. The switch is a database
// row an administrator changes on that page, so the notice names the page and, only for an
// account that may open it (`read:features`), links it. An account without the permission reads
// the same words as text: a link it cannot follow is not a destination. Pure and free of
// session state, so a server component and a client one share it.

import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { holds } from "@/components/auth/access";

import "./features.css";

export const FEATURES_HREF = "/admin/features";
/** One anchor for the whole name: underlined end to end, so the arrow reads as part of the link. */
export const FEATURES_LINK_CLASS = "features-link";
/** The page's name as the notices spell it (the backend's sentences carry the same words). */
export const FEATURES_LABEL = "Administration → Features";
export const FEATURES_PERMISSION = "read:features";

/** True when the account may open the Features page. Fails closed on an unread account. */
export function canReadFeatures(
  me: { permissions?: readonly string[] } | null | undefined,
): boolean {
  return me ? holds(me.permissions ?? [], FEATURES_PERMISSION) : false;
}

// The granted list is the one the navigation and the Administration hub test, so the link
// appears exactly where the Features entry does.

/** The `<features>` rich-text handler of a catalog message: a link, or the plain words. */
export function featuresRich(linked: boolean): { features: (chunks: ReactNode) => ReactNode } {
  return {
    features: (chunks) => (linked ? <Link className={FEATURES_LINK_CLASS} href={FEATURES_HREF}>{chunks}</Link> : <>{chunks}</>),
  };
}

/** `text` with every spelling of the page's name replaced by `featuresRich`'s answer. */
export function withFeaturesLink(text: string, linked: boolean): ReactNode {
  if (!linked || !text.includes(FEATURES_LABEL)) return text;
  const parts = text.split(FEATURES_LABEL);
  return parts.map((part, index) => (
    <Fragment key={index}>
      {index > 0 && <Link className={FEATURES_LINK_CLASS} href={FEATURES_HREF}>{FEATURES_LABEL}</Link>}
      {part}
    </Fragment>
  ));
}
