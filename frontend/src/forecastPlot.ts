import type { SeriesOption } from "echarts";

type Point = { date: string; estimate: number; lower: number; upper: number };
type Forecast = { model_id: string; family: string; points: Point[] };
export type PlotResult = {
  history?: { time: string; close: number }[];
  models?: Forecast[];
};

const familyColors: Record<string, string> = {
  ridge: "#496da8",
  arima: "#986d45",
  sarima: "#8f709e",
  naive: "#7b8a7a",
};

export function forecastPlot(result?: PlotResult) {
  const historical = result?.history || [];
  const forecasts = result?.models || [];
  const futureDates = [
    ...new Set(forecasts.flatMap((m) => m.points.map((p) => p.date))),
  ].sort();
  const dates = [...historical.map((r) => r.time), ...futureDates];
  const anchor = historical.at(-1);
  const series: SeriesOption[] = [
    {
      id: "forecast-history",
      name: "历史收盘",
      type: "line",
      data: [...historical.map((r) => r.close), ...futureDates.map(() => null)],
      symbol: "none",
      lineStyle: { color: "#304e3b", width: 2 },
      itemStyle: { color: "#304e3b" },
      ...(anchor && futureDates.length
        ? {
            markLine: {
              silent: true,
              symbol: "none",
              label: {
                formatter: "预测起点",
                position: "insideEndTop",
                color: "#78837c",
              },
              lineStyle: { color: "#a4aea7", type: "dashed" },
              data: [{ xAxis: anchor.time }],
            },
          }
        : {}),
    },
  ];
  forecasts.forEach((m, i) => {
    const color =
      familyColors[m.family] || ["#477956", "#a38b59", "#7e83a1"][i % 3];
    const byDate = new Map(m.points.map((p) => [p.date, p]));
    const prefix = historical.map((r, index) =>
      index === historical.length - 1 ? r.close : null,
    );
    const last = m.points.at(-1);
    series.push({
      id: "forecast-line-" + m.model_id,
      name: m.family,
      type: "line",
      data: [
        ...prefix,
        ...futureDates.map((d) => byDate.get(d)?.estimate ?? null),
      ],
      symbol: "circle",
      symbolSize: m.points.length === 1 ? 9 : 4,
      showAllSymbol: true,
      z: 5,
      lineStyle: { color, type: "dashed", width: 2.5 },
      itemStyle: { color },
      ...(last
        ? {
            markPoint: {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color },
              label: {
                show: true,
                position: i % 2 ? "bottom" : "top",
                distance: 12,
                color,
                backgroundColor: "rgba(255,255,255,0.9)",
                padding: [3, 5],
                formatter: `${m.family} ¥${last.estimate.toFixed(2)}`,
              },
              data: [
                {
                  name: m.family + "期末预测",
                  coord: [last.date, last.estimate],
                  value: last.estimate,
                },
              ],
            },
            ...(m.points.length === 1
              ? {
                  markLine: {
                    silent: true,
                    symbol: ["rect", "rect"],
                    symbolSize: [10, 3],
                    label: { show: false },
                    lineStyle: {
                      color,
                      width: 2,
                      type: "solid",
                      opacity: 0.65,
                    },
                    data: [
                      [
                        { coord: [last.date, last.lower] },
                        { coord: [last.date, last.upper] },
                      ],
                    ],
                  },
                }
              : {}),
          }
        : {}),
    });
    series.push({
      id: "forecast-lower-" + m.model_id,
      name: m.family + "区间下沿",
      type: "line",
      stack: "band-" + m.model_id,
      data: [
        ...prefix,
        ...futureDates.map((d) => byDate.get(d)?.lower ?? null),
      ],
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      tooltip: { show: false },
      emphasis: { disabled: true },
    });
    series.push({
      id: "forecast-width-" + m.model_id,
      name: m.family + "区间宽度",
      type: "line",
      stack: "band-" + m.model_id,
      data: [
        ...historical.map((_, index) =>
          index === historical.length - 1 ? 0 : null,
        ),
        ...futureDates.map((d) => {
          const p = byDate.get(d);
          return p ? p.upper - p.lower : null;
        }),
      ],
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color, opacity: 0.13 },
      tooltip: { show: false },
      emphasis: { disabled: true },
    });
  });
  return { dates, series, futureCount: futureDates.length };
}
