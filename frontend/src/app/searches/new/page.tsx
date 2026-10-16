import type { Metadata } from "next";
import Link from "next/link";

import { SearchEditor } from "@/components/searches/SearchEditor";
import { SearchWriteNotice } from "@/components/searches/SearchWriteNotice";
import { searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import { api } from "@/lib/api";

export const metadata: Metadata = {
  title: "New search",
};

export default async function NewSearchPage() {
  let access: SearchAccess = "unknown";
  try {
    access = searchAccessFrom(await api.caller());
  } catch {
    access = "unknown";
  }

  return (
    <>
      <PageHeader
        title="New search"
        description="A new search is saved disabled. Run it from its page to preview the result; only the saved query ever runs."
      />
      {access === "writer" ? (
        <SearchEditor mode="create" />
      ) : (
        <>
          <SearchWriteNotice unknown={access === "unknown"} />
          <p className="field-hint">
            <Link href="/searches">Back to searches</Link>
          </p>
        </>
      )}
    </>
  );
}
