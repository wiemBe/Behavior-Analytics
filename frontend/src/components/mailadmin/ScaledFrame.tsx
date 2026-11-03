"use client";

// The message is drawn at the width a mail client draws it (the frame's own layout width, from
// `--mail-frame-w`) and scaled as a whole to the column it sits in. It is never reflowed to a
// narrower width: what the recipient's client renders is what is shown, only smaller. One control
// shows it at actual size, where the stage scrolls sideways and the page does not.
//
// The scale is measured (column width over the frame's layout width) and handed to the stylesheet as
// the custom property `--mail-scale` on the stage: the one inline style on this page, because the
// value exists only at run time. Nothing else is styled from here.

import { useTranslations } from "next-intl";
import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

/** Under this much shrinkage the frame is taken to fit: sub-pixel rounding is not a scale. */
const FITS = 0.995;

export function ScaledFrame({
  html,
  actual,
  onActual,
}: {
  html: string;
  actual: boolean;
  onActual: (actual: boolean) => void;
}) {
  const t = useTranslations("mailAdmin.preview");
  const stageRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [fit, setFit] = useState(1);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    const frame = frameRef.current;
    if (!stage || !frame) return;
    const measure = () => {
      const column = stage.clientWidth;
      const natural = frame.offsetWidth;
      // Unmeasured is shown at full size, never at a guessed scale.
      const next = column > 0 && natural > 0 ? Math.min(1, column / natural) : 1;
      setFit(next >= FITS ? 1 : next);
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  const scale = actual ? 1 : fit;
  const needed = fit < 1;
  const percent = Math.floor(scale * 100);

  return (
    <div className="mail-scaled">
      {needed || actual ? (
        <div className="mail-scale-bar">
          <p className="mail-hint" role="status">
            {scale < 1 ? t("scaleFit", { percent }) : t("scaleActual")}
          </p>
          <button
            type="button"
            className="action secondary"
            aria-pressed={actual}
            onClick={() => onActual(!actual)}
          >
            {actual ? t("showFit") : t("showActual")}
          </button>
        </div>
      ) : null}
      <div
        ref={stageRef}
        className={actual ? "mail-stage mail-stage-actual" : "mail-stage"}
        style={{ "--mail-scale": String(scale) } as CSSProperties}
        {...(actual ? { role: "region", "aria-label": t("stageLabel"), tabIndex: 0 } : {})}
      >
        <iframe
          ref={frameRef}
          className="mail-frame"
          title={t("frameTitle")}
          sandbox=""
          referrerPolicy="no-referrer"
          srcDoc={html}
        />
      </div>
    </div>
  );
}
