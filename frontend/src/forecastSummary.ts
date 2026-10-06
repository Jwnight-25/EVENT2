export type ForecastPoint = {
  date: string;
  estimate: number;
  lower: number;
  upper: number;
};

export function summarizePath(points: ForecastPoint[], base: number) {
  if (
    !points.length ||
    !(base > 0) ||
    points.some((p) => ![p.estimate, p.lower, p.upper].every(Number.isFinite))
  )
    return null;
  const endpoint = points.at(-1)!;
  const change = endpoint.estimate / base - 1;
  const direction = (value: number) =>
    value > 0.01 + 1e-10
      ? "偏上行"
      : value < -0.01 - 1e-10
        ? "偏下行"
        : "小幅波动";
  const peak = points.reduce((a, p) => (p.estimate > a.estimate ? p : a));
  const trough = points.reduce((a, p) => (p.estimate < a.estimate ? p : a));
  const cuts = [
    ...new Set([
      Math.ceil(points.length / 3),
      Math.ceil((points.length * 2) / 3),
      points.length,
    ]),
  ];
  let prior = base,
    start = 0;
  const stages = cuts.map((end) => {
    const last = points[end - 1];
    const stageChange = last.estimate / prior - 1;
    const item = {
      start: points[start].date,
      end: last.date,
      estimate: last.estimate,
      change: stageChange,
      trend: direction(stageChange),
    };
    prior = last.estimate;
    start = end;
    return item;
  });
  return {
    endpoint,
    change,
    trend: direction(change),
    peak,
    trough,
    stages,
    envelopeLow: Math.min(...points.map((p) => p.lower)),
    envelopeHigh: Math.max(...points.map((p) => p.upper)),
  };
}
