// Synthetic fixtures exclusively for UI calculations, never production market data.
import test from "node:test";
import assert from "node:assert/strict";
import {
  volumeColor,
  RISE,
  FALL,
  FLAT,
  recentWindow,
  zoomWindow,
  rangeForDates,
  visibleIndices,
  priceBounds,
  panWindow,
} from "../src/chartUtils.ts";
import { forecastPlot } from "../src/forecastPlot.ts";
import { trainingParticipation } from "../src/trainingParticipation.ts";
import { summarizePath } from "../src/forecastSummary.ts";

const bar = (time: string, close = 10, open = 9) => ({
  time,
  close,
  open,
  high: Math.max(close, open) + 1,
  low: Math.min(close, open) - 1,
  volume: 100,
  amount: null,
});

test("volume follows prior close even when candle body goes the other way", () => {
  assert.equal(
    volumeColor(bar("2026-01-02", 10, 11), bar("2026-01-01", 9)),
    RISE,
  );
  assert.equal(
    volumeColor(bar("2026-01-02", 10, 9), bar("2026-01-01", 11)),
    FALL,
  );
  assert.equal(volumeColor(bar("2026-01-02", 10), bar("2026-01-01", 10)), FLAT);
  assert.equal(volumeColor(bar("2026-01-01", 10, 9)), RISE);
});

test("date selection includes all intraday bars on the end date and rejects empty ranges", () => {
  const rows = [
    bar("2026-01-02T09:31:00+08:00"),
    bar("2026-01-02T15:00:00+08:00"),
    bar("2026-01-05T09:31:00+08:00"),
  ];
  const range = rangeForDates(rows, "2026-01-02", "2026-01-02")!;
  assert.deepEqual(visibleIndices(rows.length, range), [0, 1]);
  assert.equal(rangeForDates(rows, "2026-01-03", "2026-01-04"), null);
  assert.equal(rangeForDates(rows, "2026-01-05", "2026-01-02"), null);
  assert.equal(rangeForDates([], "2026-01-02", "2026-01-05"), null);
});

test("recent window is exactly 120 bars and zoom never leaves available history", () => {
  assert.deepEqual(visibleIndices(2611, recentWindow(2611)), [2491, 2610]);
  assert.deepEqual(recentWindow(10), { start: 0, end: 100 });
  let range = recentWindow(2611);
  for (let i = 0; i < 20; i++) range = zoomWindow(range, 0.7, 2611);
  const [start, end] = visibleIndices(2611, range);
  assert.ok(end - start >= 7);
  assert.ok(range.start >= 0 && range.end <= 100);
  assert.deepEqual(zoomWindow(range, 100000, 2611), { start: 0, end: 100 });
});

test("auto price axis follows visible candles, not off-screen extremes", () => {
  const rows = [
    bar("2026-01-01", 100),
    bar("2026-01-02", 10),
    bar("2026-01-03", 11),
  ];
  const bounds = priceBounds(rows, { start: 50, end: 100 });
  assert.ok(bounds.max > 12 && bounds.max < 20);
  assert.ok(bounds.min < 8 && bounds.min >= 0);
});

test("horizontal scrollbar preserves span and stops at both history boundaries", () => {
  const range = recentWindow(2611);
  assert.deepEqual(
    visibleIndices(2611, panWindow(2611, range, 1000)),
    [1000, 1119],
  );
  assert.deepEqual(
    visibleIndices(2611, panWindow(2611, range, -100)),
    [0, 119],
  );
  assert.deepEqual(
    visibleIndices(2611, panWindow(2611, range, 10000)),
    [2491, 2610],
  );
  assert.deepEqual(panWindow(2611, { start: 0, end: 100 }, 1000), {
    start: 0,
    end: 100,
  });
});

test("forecast summary distinguishes point extrema, end interval and interval envelope", () => {
  const points = [
    { date: "2026-01-01", estimate: 103, lower: 99, upper: 108 },
    { date: "2026-01-02", estimate: 101, lower: 94, upper: 106 },
    { date: "2026-01-03", estimate: 95, lower: 90, upper: 100 },
  ];
  const result = summarizePath(points, 100)!;
  assert.equal(result.trend, "偏下行");
  assert.equal(result.peak.date, "2026-01-01");
  assert.equal(result.trough.date, "2026-01-03");
  assert.equal(result.envelopeHigh, 108);
  assert.equal(result.endpoint.upper, 100);
  assert.equal(result.stages[1].trend, "偏下行");
  assert.equal(result.stages[2].estimate, 95);
});

test("one-day, exactly one-percent, and empty forecast inputs are handled explicitly", () => {
  const point = { date: "2026-01-01", estimate: 101, lower: 95, upper: 105 };
  const result = summarizePath([point], 100)!;
  assert.equal(result.trend, "小幅波动");
  assert.equal(result.stages.length, 1);
  assert.equal(summarizePath([], 100), null);
  assert.equal(summarizePath([point], 0), null);
});

test("single-day forecast retains the real endpoint, an anchor, and visible interval marks", () => {
  const history = Array.from({ length: 120 }, (_, i) => ({
    time: `history-${i}`,
    close: 40,
  }));
  const plot = forecastPlot({
    history,
    models: [
      {
        family: "ridge",
        model_id: "r1",
        points: [
          { date: "2026-10-08", estimate: 41.29, lower: 40.56, upper: 42.04 },
        ],
      },
    ],
  });
  assert.equal(plot.futureCount, 1);
  const line: any = plot.series.find((s: any) => s.id === "forecast-line-r1");
  assert.deepEqual(line.data.slice(-2), [40, 41.29]);
  assert.equal(line.data.filter((v: any) => v !== null).length, 2);
  assert.equal(line.symbol, "circle");
  assert.deepEqual(line.markPoint.data[0].coord, ["2026-10-08", 41.29]);
  assert.deepEqual(
    line.markLine.data[0].map((p: any) => p.coord),
    [
      ["2026-10-08", 40.56],
      ["2026-10-08", 42.04],
    ],
  );
  const lower: any = plot.series.find((s: any) => s.id === "forecast-lower-r1");
  const width: any = plot.series.find((s: any) => s.id === "forecast-width-r1");
  assert.equal(lower.data.at(-1) + width.data.at(-1), 42.04);
  assert.deepEqual(
    visibleIndices(plot.dates.length, recentWindow(plot.dates.length, 31)),
    [90, 120],
  );
});

test("different models align by prediction date without inventing values or losing later dates", () => {
  const plot = forecastPlot({
    history: [{ time: "2026-01-01", close: 10 }],
    models: [
      {
        model_id: "r",
        family: "ridge",
        points: [{ date: "2026-01-02", estimate: 11, lower: 9, upper: 12 }],
      },
      {
        model_id: "s",
        family: "sarima",
        points: [
          { date: "2026-01-02", estimate: 10.5, lower: 9, upper: 12 },
          { date: "2026-01-03", estimate: 12, lower: 10, upper: 13 },
        ],
      },
    ],
  });
  assert.deepEqual(plot.dates, ["2026-01-01", "2026-01-02", "2026-01-03"]);
  const ridge: any = plot.series.find((s: any) => s.id === "forecast-line-r");
  const sarima: any = plot.series.find((s: any) => s.id === "forecast-line-s");
  assert.deepEqual(ridge.data, [10, 11, null]);
  assert.deepEqual(sarima.data, [10, 10.5, 12]);
  assert.notEqual(ridge.lineStyle.color, sarima.lineStyle.color);
});

test("participation distinguishes parameter successes from a later failed evaluation", () => {
  const summary = trainingParticipation(
    ["arima", "ridge"],
    "one_month",
    [
      {
        family: "arima",
        horizon: "one_month",
        status: "succeeded",
        report: {},
      },
      {
        family: "arima",
        horizon: "one_month",
        status: "failed",
        report: { error: "parameter fit" },
      },
      {
        family: "arima",
        horizon: "one_month",
        status: "failed",
        report: { phase: "acceptance", error: "not converged" },
      },
      {
        family: "ridge",
        horizon: "one_month",
        status: "succeeded",
        report: {},
      },
      { family: "arima", horizon: "next_day", status: "succeeded", report: {} },
    ],
    ["ridge", "naive"],
  );
  assert.deepEqual(
    summary.map((p) => [p.family, p.trials, p.succeeded, p.failed, p.saved]),
    [
      ["arima", 2, 1, 1, false],
      ["ridge", 1, 1, 0, true],
    ],
  );
  assert.match(summary[0].reason, /not converged/);
  assert.equal(
    summary.some((p) => p.family === "sarima"),
    false,
  );
});
