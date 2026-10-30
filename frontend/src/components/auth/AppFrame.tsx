"use client";

import Link from "next/link";


// The shell, except where the account is not allowed to see it. Sign-in has
// no navigation. A password that must change, and an account with no
// permission, replace the page they asked for.

import { Suspense, useEffect, useRef, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { hasAnyAccess, visibleNav } from "./access";
import { NoRole } from "./NoRole";
import { PasswordChange } from "./PasswordChange";
import { useSession } from "./session";
import styles from "./auth.module.css";
import { HistoryFirstRun } from "@/components/history/HistoryFirstRun";
import { AppNav } from "@/components/shell/AppNav";
import { TopBar } from "@/components/shell/TopBar";
import { NAV } from "@/lib/nav";

function onLoginPath(pathname: string): boolean {
  return pathname === "/login" || pathname.startsWith("/login/");
}

export function AppFrame({ children }: { children: ReactNode }) {
  const t = useTranslations("auth");
  const common = useTranslations("common");
  const pathname = usePathname() ?? "/";
  const session = useSession();

  // Leaving the sign-in page while the session still reads as signed out is wrong: the page is
  // left because a sign-in succeeded. Read `/me` once more, so an earlier 401 cannot keep the
  // "Sign-in is required." gate over a valid cookie until a reload (HOTFIX-1, F5).
  const cameFromLogin = useRef(false);
  const { status, reload } = session;
  useEffect(() => {
    if (onLoginPath(pathname)) {
      cameFromLogin.current = true;
    } else if (status === "ready") {
      cameFromLogin.current = false;
    } else if (status === "anonymous" && cameFromLogin.current) {
      cameFromLogin.current = false;
      void reload().catch(() => {
        /* the provider records the failure and the frame shows it */
      });
    }
  }, [pathname, status, reload]);

  if (onLoginPath(pathname)) {
    return <div className={styles.gate}>{children}</div>;
  }

  if (session.status === "ready" && session.me?.must_change_password) {
    return (
      <div className={styles.gate}>
        <Suspense fallback={<p className="notice" role="status">{t("passwordLoading")}</p>}>
          <PasswordChange required />
        </Suspense>
      </div>
    );
  }

  if (session.status === "ready" && session.me && !hasAnyAccess(session.me)) {
    return (
      <div className={styles.gate}>
        <NoRole me={session.me} />
      </div>
    );
  }

  if (session.status === "anonymous") {
    return (
      <div className={styles.gate}>
        <p className="notice" role="status">
          {t("signInRequired")}
        </p>
      </div>
    );
  }

  if (session.status === "error") {
    return (
      <div className={styles.gate}>
        <div className="notice" role="alert">
          <p>
            <strong>{t("sessionFailed")}</strong>
          </p>
          <p>
            {t("sessionFailedDetail", {
              reason: session.error ? ` ${session.error}` : "",
            })}
          </p>
          <p>
            <button type="button" className="action secondary" onClick={() => void session.reload()}>
              {common("tryAgain")}
            </button>
          </p>
        </div>
      </div>
    );
  }

  const granted = session.status === "ready" && session.me ? session.me.permissions : [];

  return (
    <>
      <header className="shell-head">
        <Link href="/behavior" className="brand plain">
          <span className="brand-name">{common("product")}</span>
          <span className="brand-sub">{t("brandSubtitle")}</span>
        </Link>
        <TopBar />
      </header>
      <div className="layout">
        <aside className="sidebar">
          <AppNav groups={visibleNav(NAV, granted)} />
        </aside>
        <main className="main" id="content">
          <div className="page">{children}</div>
        </main>
      </div>
      <HistoryFirstRun />
    </>
  );
}
