// One banner, every reason, in the order the caller produced them.

import styles from "./auth.module.css";

export function ReasonList({
  tone,
  heading,
  lines,
}: {
  tone: "ok" | "warn";
  heading?: string;
  lines: string[];
}) {
  if (!heading && lines.length === 0) return null;
  return (
    <div className={`banner ${tone}`} role={tone === "ok" ? "status" : "alert"}>
      <div>
        {heading ? (
          <p>
            <strong>{heading}</strong>
          </p>
        ) : null}
        {lines.length > 0 ? (
          <ul className={styles.reasons}>
            {lines.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
