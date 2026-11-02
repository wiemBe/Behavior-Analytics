"use client";

// The small text link a page header shows to its guide section. The accessible name carries the
// section's title; the titles come from `guideLinks` (a few words each), not from the guide text,
// which the browser never receives.
//
// The link is a first-visit pointer, not a standing control (GUIDE-3): once an account has been
// shown a section's link, the page does not render it again, and the shell's fixed Guide entry is
// the way back. "Shown" is stored on the account (`guide_seen`), so it follows the analyst across
// browsers.
//
//   - While the session is still being read nothing renders, so the link never appears and then
//     vanishes.
//   - A section already in `guide_seen` renders nothing.
//   - A section not in it renders, is recorded once (fire-and-forget; a failure is ignored and
//     never shown), and then stays for the rest of this page view even though the record and the
//     session now say "seen". Only the next visit hides it.
//   - `guide_seen` null or absent (a principal without an account, an older backend) means the
//     choice cannot be stored, so the link always renders and nothing is recorded.
//   - With no session provider at all (a bare component) the link renders, as before.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { useSession } from "@/components/auth/session";
import { own } from "@/i18n/ownKey";
import { recordGuideSeen } from "@/lib/api";
import { rawMessage } from "@/lib/guideText";

export function GuideLink({ section }: { section: string }) {
  const t = useTranslations("guideLinks");
  const session = useSession();
  const title = own(rawMessage(t, "titles") as Record<string, string>, section);
  const known = title !== undefined;

  const detached = session.detached === true;
  const ready = detached || session.status === "ready";
  const seen = session.me?.guide_seen;
  const recordable = !detached && Array.isArray(seen);

  // The decision is taken once per page view and section, on the first ready read, and kept.
  const [decided, setDecided] = useState<{ section: string; show: boolean } | null>(null);
  const current = decided?.section === section ? decided : null;
  useEffect(() => {
    if (current !== null || !ready || !known) return;
    setDecided({ section, show: !recordable || !seen!.includes(section) });
  }, [current, ready, known, recordable, seen, section]);

  const show = current?.show ?? false;
  const recorded = useRef<string | null>(null);
  const note = session.noteGuideSeen;
  useEffect(() => {
    if (!show || !recordable || recorded.current === section) return;
    recorded.current = section;
    note?.(section);
    recordGuideSeen(section).catch(() => {
      /* a link that could not be recorded shows again next time; nothing to tell the analyst */
    });
  }, [show, recordable, section, note]);

  // A section the catalog does not know shows no link rather than a link to a 404.
  if (!known || !show) return null;
  return (
    <Link className="guide-link" href={`/guide/${section}`} aria-label={t("named", { topic: title })}>
      {t("label")}
    </Link>
  );
}
