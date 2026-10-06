export type Stock = {
  id: string;
  code: string;
  name: string;
  exchange: string;
  data_cutoff: string | null;
  intervals: string[];
};
export type Bar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number | null;
  incomplete?: boolean;
};
export type Job = {
  id: string;
  status: string;
  stage: string;
  progress: number | null;
  error: string | null;
  created_at: string;
  cancel_requested: boolean;
  result: Record<string, string>;
};
export type Model = {
  id: string;
  family: string;
  horizon: string;
  selected: boolean;
  reason: string;
  metrics: Record<string, any>;
  parameters: Record<string, any>;
  created_at: string;
  run_id: string;
};
export type ModelFamily = {
  id: string;
  name: string;
  available: boolean;
  missing_dependencies: string[];
  message: string;
};
export type Prediction = {
  id: string;
  horizon: string;
  created_at: string;
  result: Record<string, any>;
  config: Record<string, any>;
  ai_status: string;
  ai_analyses?: any[];
  actual_bars?: Bar[];
};
export const horizons: Record<string, string> = {
  next_day: "下一交易日",
  one_month: "一个月 · 20交易日",
  three_months: "三个月 · 60交易日",
};
export const statusText: Record<string, string> = {
  queued: "等待处理",
  running: "进行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      ...(options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.message || `服务请求失败（${response.status}）`);
  }
  return response.json();
}
export function post<T>(path: string, body: unknown) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
export const money = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
export const pct = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? (value * 100).toFixed(1) + "%"
    : "—";
export const probability = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? value < 0.0001
      ? "<0.0001"
      : value.toFixed(4)
    : "未计算";
export const score = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
