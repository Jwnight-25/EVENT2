import type { Bar } from "./api";

export type WindowRange = { start: number; end: number };
export const RISE = "#d56960";
export const FALL = "#438c70";
export const FLAT = "#96a29a";
const clamp = (v: number, low: number, high: number) =>
  Math.max(low, Math.min(high, v));

export function volumeColor(row: Bar, previous?: Bar) {
  const reference = previous?.close ?? row.open;
  return row.close > reference ? RISE : row.close < reference ? FALL : FLAT;
}

export function visibleIndices(
  count: number,
  range: WindowRange,
): [number, number] {
  const last = Math.max(0, count - 1);
  return [
    Math.round((clamp(range.start, 0, 100) / 100) * last),
    Math.round((clamp(range.end, 0, 100) / 100) * last),
  ];
}

export function recentWindow(count: number, bars = 120): WindowRange {
  return {
    start: count > bars ? ((count - bars) / (count - 1)) * 100 : 0,
    end: 100,
  };
}

export function zoomWindow(
  range: WindowRange,
  factor: number,
  count: number,
): WindowRange {
  const width = clamp(
    (range.end - range.start) * factor,
    Math.min(100, (7 / Math.max(1, count - 1)) * 100),
    100,
  );
  const start = clamp((range.start + range.end - width) / 2, 0, 100 - width);
  return { start, end: start + width };
}

export function rangeForDates(
  rows: Bar[],
  start: string,
  end: string,
): WindowRange | null {
  if (!rows.length || !start || !end || start > end) return null;
  const first = rows.findIndex((r) => r.time.slice(0, 10) >= start);
  let last = rows.length - 1;
  while (last >= 0 && rows[last].time.slice(0, 10) > end) last--;
  if (first < 0 || last < first) return null;
  // A single session stays visible; category dataZoom accepts equal bounds.
  return {
    start: (first / Math.max(1, rows.length - 1)) * 100,
    end: (last / Math.max(1, rows.length - 1)) * 100,
  };
}

export function priceBounds(rows: Bar[], range: WindowRange) {
  const [start, end] = visibleIndices(rows.length, range);
  const visible = rows.slice(start, end + 1);
  if (!visible.length) return { min: 0, max: 1 };
  const low = Math.min(...visible.map((r) => r.low)),
    high = Math.max(...visible.map((r) => r.high));
  const pad = Math.max((high - low) * 0.07, high * 0.002, 0.01);
  return { min: Math.max(0, low - pad), max: high + pad };
}
