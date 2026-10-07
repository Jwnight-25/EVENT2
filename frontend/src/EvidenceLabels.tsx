export function EvidenceLabels({
  evidence,
}: {
  evidence?: Record<string, any>;
}) {
  if (!evidence) return <small className="muted">历史结果未记录分项证据</small>;
  return (
    <div className="evidence-labels">
      <span
        className={
          "pill" +
          (evidence.baseline_status === "significant_gain" ? "" : " gray")
        }
      >
        {evidence.baseline_label}
      </span>
      <span className={"pill" + (evidence.interval_stable ? "" : " gray")}>
        {evidence.interval_label}
      </span>
    </div>
  );
}
