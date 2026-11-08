"use client";

// The low-volume model section of `/behavior/noise/[id]` (SOC-17e-ui).
//
// A shadow view of the SOC-17e count model: no detector uses it and it opens no alert. It reads
// one route on mount and draws, in order, the overall state with its sentence and window, the
// proposed level shifts, a weekday x hour grid per metric, the selected cell's numbers and the
// calibration against closed NOISE incidents. A number the model did not measure is "—" with its
// reason; a refused calibration carries its sentence and no count. The model route being switched
// off is a stated condition, not an error.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useState } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type NoiseModel, type NoiseModelCell } from "@/lib/api";
import {
  MISSING,
  MODEL_METRICS,
  cellIndex,
  cellKey,
  count,
  hourLabel,
  modelFailure,
  num,
  precise,
  tailBin,
  tailTotal,
  weightPercent,
  type ModelFailure,
} from "@/lib/noiseModel";

import "@/components/ui/controls.css";
import "./noise.css";

type Load =
  | { status: "loading" }
  | { status: "ready"; model: NoiseModel }
  | { status: "failed"; failure: ModelFailure };

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];

export function NoiseModelView({ logSourceId }: { logSourceId: string }) {
  const t = asTextTranslator(useTranslations("noise.model"));
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setLoad({ status: "loading" });
    setSelected(null);
    api
      .noiseModel(logSourceId)
      .then((model) => {
        if (live) setLoad({ status: "ready", model });
      })
      .catch((err: unknown) => {
        if (!live) return;
        const failure =
          err instanceof ApiError ? modelFailure(err.status, err.message) : modelFailure(null, undefined);
        setLoad({ status: "failed", failure });
      });
    return () => {
      live = false;
    };
  }, [logSourceId, attempt]);

  return (
    <section aria-labelledby="noise-model-title">
      <h2 id="noise-model-title" className="section-title">
        {t("title")}
      </h2>
      <p className="noise-footnote noise-model-shadow">{t("shadow")}</p>
      {load.status === "loading" && (
        <div className="notice" role="status" aria-busy="true">
          <p>{t("loading")}</p>
        </div>
      )}
      {load.status === "failed" && (
        <Failure failure={load.failure} onRetry={() => setAttempt((n) => n + 1)} />
      )}
      {load.status === "ready" && (
        <Ready model={load.model} selected={selected} onSelect={setSelected} />
      )}
    </section>
  );
}

function Failure({ failure, onRetry }: { failure: ModelFailure; onRetry: () => void }) {
  const t = asTextTranslator(useTranslations("noise.model"));
  return (
    <div className="notice" role={failure === "disabled" ? "status" : "alert"}>
      <p>
        <strong>{t(`${failure}.title`)}</strong>
      </p>
      <p>{t(`${failure}.text`)}</p>
      {failure === "failed" && (
        <p>
          <button type="button" className="action secondary" onClick={onRetry}>
            {t("retry")}
          </button>
        </p>
      )}
    </div>
  );
}

function Ready({
  model,
  selected,
  onSelect,
}: {
  model: NoiseModel;
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const shifts = model.cells.filter((c) => c.level_shift !== null);
  const chosen = selected ? (model.cells.find((c) => cellKey(c) === selected) ?? null) : null;

  const focusCell = useCallback(
    (key: string) => {
      onSelect(key);
      requestAnimationFrame(() => document.getElementById(`nm-${key}`)?.focus());
    },
    [onSelect],
  );

  return (
    <div className="stack-6">
      <Summary model={model} />
      <Shifts cells={shifts} onPick={focusCell} />
      {MODEL_METRICS.map((metric) => (
        <Grid
          key={metric}
          metric={metric}
          cells={model.cells}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
      <Legend />
      <div aria-live="polite">
        <Detail cell={chosen} />
      </div>
      <Calibration model={model} />
    </div>
  );
}

function Summary({ model }: { model: NoiseModel }) {
  const t = asTextTranslator(useTranslations("noise.model"));
  const tone = model.state === "OK" ? "" : " warn";
  return (
    <div className="noise-model-summary">
      <p className="noise-model-state">
        <span className={`pill pill-quiet${tone}`}>{t(`state.${model.state}`)}</span>{" "}
        {model.reason && <span>{model.reason.text}</span>}
      </p>
      {model.state === "PARTIAL" && !model.reason && (
        <div className="banner warn" role="status">
          <p>
            <strong>{t("partial")}</strong>{" "}
            {t("partialText", { covered: model.covered_days, window: model.window_days })}
          </p>
        </div>
      )}
      <dl className="noise-model-facts">
        <div>
          <dt>{t("facts.window")}</dt>
          <dd>{t("facts.windowValue", { covered: model.covered_days, window: model.window_days })}</dd>
        </div>
        <div>
          <dt>{t("facts.span")}</dt>
          <dd>
            {model.first_bucket && model.last_bucket ? (
              <>
                <UtcTime value={model.first_bucket} /> {t("facts.to")} <UtcTime value={model.last_bucket} />
              </>
            ) : (
              MISSING
            )}
          </dd>
        </div>
        <div>
          <dt>{t("facts.p")}</dt>
          <dd>{t("facts.pValue", { p: precise(model.p_threshold) })}</dd>
        </div>
        <div>
          <dt>{t("facts.k")}</dt>
          <dd>{t("facts.kValue", { k: model.shrinkage_k })}</dd>
        </div>
      </dl>
    </div>
  );
}

function Shifts({ cells, onPick }: { cells: NoiseModelCell[]; onPick: (key: string) => void }) {
  const t = asTextTranslator(useTranslations("noise.model"));
  if (cells.length === 0) {
    return <p className="noise-footnote">{t("shifts.none")}</p>;
  }
  return (
    <div className="noise-model-shifts">
      <h3>{t("shifts.title", { count: cells.length })}</h3>
      <p className="noise-footnote">{t("shifts.intro")}</p>
      <ul>
        {cells.map((c) => {
          const key = cellKey(c);
          return (
            <li key={key}>
              <Link
                href={`#nm-${key}`}
                onClick={(e) => {
                  e.preventDefault();
                  onPick(key);
                }}
              >
                {t("shifts.item", {
                  metric: t(`metric.${c.metric}`),
                  weekday: t(`weekday.${c.weekday}`),
                  hour: hourLabel(c.hour),
                })}
              </Link>{" "}
              <span className="muted num">
                {c.level_shift
                  ? t("shifts.change", {
                      recent: num(c.level_shift.recent_median),
                      cell: num(c.level_shift.cell_median),
                    })
                  : null}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Grid({
  metric,
  cells,
  selected,
  onSelect,
}: {
  metric: string;
  cells: NoiseModelCell[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const t = asTextTranslator(useTranslations("noise.model"));
  const uid = useId();
  const index = cellIndex(cells, metric);
  const unit = t.has?.(`metric.unit.${metric}`) ? t(`metric.unit.${metric}`) : "";
  const name = t.has?.(`metric.${metric}`) ? t(`metric.${metric}`) : metric;
  return (
    <div className="noise-model-block">
      <h3 id={`${uid}-h`}>
        {name}
      </h3>
      <p className="noise-footnote noise-heat-note">{t("grid.unitLine", { unit })}</p>
      {index.size === 0 ? (
        <p className="noise-footnote">{t("grid.empty")}</p>
      ) : (
        <TableScroll label={name}>
          <table className="noise-heat" aria-labelledby={`${uid}-h`}>
            <caption className="sr-only">{t("grid.caption", { metric: name })}</caption>
            <thead>
              <tr>
                <th scope="col" className="noise-heat-corner">
                  <span aria-hidden="true">{t("grid.cornerUtc")}</span>
                  <span className="sr-only">{t("grid.weekdayHead")}</span>
                </th>
                {HOURS.map((h) => (
                  <th key={h} scope="col" className="noise-heat-hour">
                    {String(h).padStart(2, "0")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {WEEKDAYS.map((wd) => (
                <tr key={wd}>
                  <th scope="row">{t(`weekday.${wd}`)}</th>
                  {HOURS.map((h) => {
                    const cell = index.get(`${wd}-${h}`);
                    return (
                      <td key={h}>
                        {cell ? (
                          <CellButton
                            cell={cell}
                            name={name}
                            on={selected === cellKey(cell)}
                            onSelect={onSelect}
                          />
                        ) : (
                          <span className="noise-heat-gap" aria-hidden="true" />
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </div>
  );
}

function cellClass(cell: NoiseModelCell): string {
  if (cell.state === "OK" || cell.state === "PARTIAL") {
    const total = tailTotal(cell);
    if (total !== null) return `noise-heat-cell bin-${tailBin(total)}`;
  }
  return cell.state === "NOT_JUDGED"
    ? "noise-heat-cell not-judged"
    : "noise-heat-cell insufficient";
}

function CellButton({
  cell,
  name,
  on,
  onSelect,
}: {
  cell: NoiseModelCell;
  name: string;
  on: boolean;
  onSelect: (key: string | null) => void;
}) {
  const t = asTextTranslator(useTranslations("noise.model"));
  const key = cellKey(cell);
  const total = tailTotal(cell);
  const shift = cell.level_shift !== null;
  return (
    <button
      type="button"
      id={`nm-${key}`}
      className={`${cellClass(cell)}${shift ? " shift" : ""}${on ? " on" : ""}`}
      aria-pressed={on}
      aria-label={`${t("grid.cellLabel", {
        metric: name,
        weekday: t(`weekday.${cell.weekday}`),
        hour: hourLabel(cell.hour),
        state: t(`state.${cell.state}`),
      })}, ${total === null ? t("grid.noTails") : t("grid.withTails", { total })}${shift ? `, ${t("legend.shift")}` : ""}`}
      onClick={() => onSelect(on ? null : key)}
    >
      {total === null ? MISSING : total}
    </button>
  );
}

function Legend() {
  const t = asTextTranslator(useTranslations("noise.model.legend"));
  return (
    <ul className="noise-heat-legend" aria-label={t("title")}>
      {(["bin0", "bin1", "bin2", "bin3"] as const).map((b, i) => (
        <li key={b}>
          <span className={`noise-heat-swatch bin-${i}`} aria-hidden="true" /> {t(b)}
        </li>
      ))}
      <li>
        <span className="noise-heat-swatch insufficient" aria-hidden="true" /> {t("insufficient")}
      </li>
      <li>
        <span className="noise-heat-swatch not-judged" aria-hidden="true" /> {t("notJudged")}
      </li>
      <li>
        <span className="noise-heat-swatch bin-0 shift" aria-hidden="true" /> {t("shift")}
      </li>
    </ul>
  );
}

function Detail({ cell }: { cell: NoiseModelCell | null }) {
  const t = asTextTranslator(useTranslations("noise.model"));
  if (!cell) {
    return (
      <div className="noise-model-detail empty">
        <p className="muted">{t("detail.none")}</p>
      </div>
    );
  }
  const s = cell.shrinkage;
  const unit = t.has?.(`metric.unit.${cell.metric}`) ? t(`metric.unit.${cell.metric}`) : "";
  const name = t.has?.(`metric.${cell.metric}`) ? t(`metric.${cell.metric}`) : cell.metric;
  const shift = cell.level_shift;
  return (
    <div className="noise-model-detail">
      <h3>
        {t("detail.heading", {
          metric: name,
          weekday: t(`weekday.${cell.weekday}`),
          hour: hourLabel(cell.hour),
        })}
      </h3>
      <dl className="noise-model-facts">
        <div>
          <dt>{t("detail.state")}</dt>
          <dd>
            <span className={`pill pill-quiet${cell.state === "OK" ? "" : " warn"}`}>
              {t(`state.${cell.state}`)}
            </span>
          </dd>
        </div>
        {cell.reason && (
          <div>
            <dt>{t("detail.reason")}</dt>
            <dd>{cell.reason.text}</dd>
          </div>
        )}
        <div>
          <dt>{t("detail.family")}</dt>
          <dd>{cell.model ? t(`detail.${cell.model}`) : t("detail.notFit")}</dd>
        </div>
        <div>
          <dt>{t("detail.samples")}</dt>
          <dd className="num">{t("detail.samplesValue", { count: count(cell.sample_count) })}</dd>
        </div>
        <div>
          <dt>{t("detail.mean")}</dt>
          <dd className="num">{cell.mean === null ? MISSING : `${num(cell.mean)} ${unit}`}</dd>
        </div>
        <div>
          <dt>{t("detail.dispersion")}</dt>
          <dd className="num">
            {cell.dispersion === null ? MISSING : `${num(cell.dispersion)} (${t("detail.dispersionNote")})`}
          </dd>
        </div>
        <div>
          <dt>{t("detail.rawMedian")}</dt>
          <dd className="num">{s.raw_median === null ? MISSING : `${num(s.raw_median)} ${unit}`}</dd>
        </div>
        <div>
          <dt>{t("detail.estimate")}</dt>
          <dd className="num">
            {s.estimate === null ? MISSING : `${num(s.estimate)} ${unit}`}
            <span className="cell-sub">
              {t("detail.estimateWeight", {
                weight: weightPercent(s.weight),
                n: s.n,
                k: s.k,
                pooled: s.pooled_median === null ? MISSING : num(s.pooled_median),
              })}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("detail.tails")}</dt>
          <dd className="num">
            {cell.upper_tail_below === null || cell.lower_tail_below === null ? (
              MISSING
            ) : (
              <>
                {t("detail.upper", { count: count(cell.upper_tail_below) })}
                <span className="cell-sub">{t("detail.lower", { count: count(cell.lower_tail_below) })}</span>
                <span className="cell-sub">{t("detail.scored", { count: count(cell.buckets_scored) })}</span>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>{t("detail.levelShift")}</dt>
          <dd>
            {shift ? (
              <>
                {t("detail.shiftText", {
                  recent: num(shift.recent_median),
                  cell: num(shift.cell_median),
                  p05: num(shift.p05),
                  p95: num(shift.p95),
                  recentSamples: shift.recent_samples,
                  cellSamples: shift.cell_samples,
                })}{" "}
                <UtcTime value={shift.started_at} />.
                <span className="cell-sub">{t("detail.shiftNote")}</span>
              </>
            ) : (
              t("detail.noShift")
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function Calibration({ model }: { model: NoiseModel }) {
  const t = asTextTranslator(useTranslations("noise.model.calibration"));
  const { label } = useEnumLabels();
  return (
    <div className="noise-model-block">
      <h3>{t("title")}</h3>
      <p className="noise-footnote">{t("intro")}</p>
      {model.calibration_reason && (
        <div className="banner warn" role="status">
          <p>
            <strong>{t("refused")}</strong> {model.calibration_reason.text}
          </p>
        </div>
      )}
      {model.calibration.length === 0 ? (
        <p className="noise-footnote">{t("none")}</p>
      ) : (
        <TableScroll label={t("caption")}>
          <table className="table-dense noise-table">
            <caption className="sr-only">{t("caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("detector")}</th>
                <th scope="col" className="num">
                  {t("flags")}
                  <span className="cell-sub">{t("flagsUnit")}</span>
                </th>
                <th scope="col" className="num">
                  {t("noiseFlags")}
                  <span className="cell-sub">{t("noiseFlagsUnit")}</span>
                </th>
                <th scope="col">{t("note")}</th>
              </tr>
            </thead>
            <tbody>
              {model.calibration.map((row) => {
                const refused = row.replayed_flags === null || row.noise_flags === null;
                return (
                  <tr key={row.anomaly_type}>
                    <th scope="row">{label("anomalyType", row.anomaly_type)}</th>
                    <td className="num">{refused ? MISSING : count(row.replayed_flags)}</td>
                    <td className="num">{refused ? MISSING : count(row.noise_flags)}</td>
                    <td>
                      {row.excluded_by_direction
                        ? t("excluded")
                        : refused
                          ? (row.reason?.text ?? t("refused"))
                          : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      )}
    </div>
  );
}
