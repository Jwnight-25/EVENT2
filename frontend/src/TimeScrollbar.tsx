import type { CSSProperties } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { panWindow, visibleIndices, type WindowRange } from "./chartUtils";

export function TimeScrollbar({
  dates,
  range,
  onChange,
  label,
  inset = 55,
}: {
  dates: string[];
  range: WindowRange;
  onChange: (range: WindowRange) => void;
  label: string;
  inset?: number;
}) {
  const [first, last] = visibleIndices(dates.length, range);
  const span = last - first + 1;
  const maximum = Math.max(0, dates.length - span);
  const move = (position: number) =>
    onChange(panWindow(dates.length, range, position));
  return (
    <div
      className="chart-scrollbar"
      style={
        {
          "--scrollbar-inset": `${inset}px`,
          "--scrollbar-thumb": `${Math.min(100, (span / Math.max(1, dates.length)) * 100)}%`,
        } as CSSProperties
      }
    >
      <div className="chart-scrollbar-track">
        <button
          className="scrollbar-arrow"
          aria-label={label + "：前一窗口"}
          disabled={first === 0}
          onClick={() => move(first - span)}
        >
          <ChevronLeft size={13} />
        </button>
        <input
          type="range"
          aria-label={label}
          aria-valuetext={`${dates[first] || ""} 至 ${dates[last] || ""}，${span}个时间点`}
          min={0}
          max={maximum}
          step={1}
          value={first}
          disabled={maximum === 0}
          onChange={(event) => move(Number(event.target.value))}
        />
        <button
          className="scrollbar-arrow"
          aria-label={label + "：后一窗口"}
          disabled={last >= dates.length - 1}
          onClick={() => move(first + span)}
        >
          <ChevronRight size={13} />
        </button>
      </div>
      <div className="chart-scrollbar-caption" aria-live="polite">
        <span>
          {dates[first]?.slice(0, 10)} — {dates[last]?.slice(0, 10)}
        </span>
        <span>左右拖动浏览 · 滚轮缩放</span>
      </div>
    </div>
  );
}
