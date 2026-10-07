type Trial = {
  family: string;
  horizon: string;
  status: string;
  report: { phase?: string; error?: string };
};

export function trainingParticipation(
  families: string[],
  horizon: string,
  trials: Trial[],
  savedFamilies: string[],
) {
  return families
    .filter((family) => family !== "naive")
    .map((family) => {
      const records = trials.filter(
        (t) => t.family === family && t.horizon === horizon,
      );
      const tuning = records.filter((t) => t.report.phase !== "acceptance");
      const finalFailure = records.find(
        (t) => t.report.phase === "acceptance" && t.status === "failed",
      );
      const saved = savedFamilies.includes(family);
      const errors = [
        ...new Set(
          records
            .filter((t) => t.status === "failed")
            .map((t) => t.report.error)
            .filter(Boolean),
        ),
      ];
      return {
        family,
        trials: tuning.length,
        succeeded: tuning.filter((t) => t.status === "succeeded").length,
        failed: tuning.filter((t) => t.status === "failed").length,
        saved,
        reason: saved
          ? "已保存最佳参数版本，见下方对比"
          : finalFailure
            ? "最终评估拟合失败：" + finalFailure.report.error
            : tuning.length && !tuning.some((t) => t.status === "succeeded")
              ? "参数试验均失败：" + errors.join("；")
              : tuning.length
                ? "尚无完成的评估版本，请查看任务状态或实验报告"
                : "该周期没有参数试验，请查看任务状态或实验报告",
      };
    });
}
