// A schedule as an analyst reads it: a preset name, or "Custom" with the cron
// kept visible. Shared by the catalog and the search page so the same cron is
// never worded two ways.

import "@/components/ui/controls.css";

import { cronToPreset } from "@/lib/searchCadence";

export function ScheduleLabel({ cron }: { cron: string }) {
  const preset = cronToPreset(cron);
  if (preset) return <>Every {preset.label}</>;
  return (
    <>
      Custom <code className="cron">{cron}</code>
    </>
  );
}
