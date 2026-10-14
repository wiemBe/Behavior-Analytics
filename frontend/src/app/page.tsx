// The Home: the dashboard at `/`.
//
// The question: what needs me now? The first screen is the key figures and the top of the
// fleet charts. This is only the shell; the widgets are client components that read their
// own data, so the top bar's refresh reaches every one of them and a refused or failed
// read stays inside the widget that made it.

import type { Metadata } from "next";

import { HomeDashboard } from "@/components/home/HomeDashboard";

export const metadata: Metadata = {
  title: "Dashboard",
};

export default function HomePage() {
  return <HomeDashboard />;
}
