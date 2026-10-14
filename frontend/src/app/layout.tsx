import "./globals.css";
import type { Metadata } from "next";

import { AppNav } from "@/components/shell/AppNav";
import { DataAgeProvider } from "@/components/shell/AutoRefresh";
import { TopBar } from "@/components/shell/TopBar";

export const metadata: Metadata = {
  title: {
    default: "Behavior Analytics",
    template: "%s · Behavior Analytics",
  },
  description:
    "Behavioral anomaly detection and investigation for on-premises QRadar: " +
    "seasonal volume baselines, an explicit anomaly lifecycle, and bounded " +
    "evidence answering what changed during an anomalous interval.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <DataAgeProvider>
          {/* The shell's first row: the brand over the sidebar column, the top
              bar to its right. A <header>, not a heading: each page's own title
              is the document's <h1>, and the shell must not take that level. */}
          <header className="shell-head">
            <a href="/behavior" className="brand plain">
              <span className="brand-name">Behavior Analytics</span>
              <span className="brand-sub">QRadar Anomaly &amp; Investigation</span>
            </a>
            <TopBar />
          </header>
          <div className="layout">
            <aside className="sidebar">
              <AppNav />
            </aside>
            <main className="main" id="content">
              <div className="page">{children}</div>
            </main>
          </div>
        </DataAgeProvider>
      </body>
    </html>
  );
}
