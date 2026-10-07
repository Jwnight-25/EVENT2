import { useState } from "react";
import { api, post, type Stock } from "./api";
import { Modal, useLoad } from "./components";

export function Maintenance({
  stock,
  basis,
  close,
}: {
  stock?: Stock;
  basis: string;
  close: () => void;
}) {
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File>();
  const [volumeUnit, setVolumeUnit] = useState("shares");
  const { data, error: loadError } = useLoad(
    () =>
      stock
        ? api<any>(
            `/maintenance/status?stock_id=${stock.id}&price_basis=${basis}`,
          )
        : Promise.resolve(undefined),
    [stock?.id, basis, version],
  );
  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      setVersion((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="数据与备份" close={close}>
      {data && (
        <>
          <h3>{stock?.name} · 当前运行数据</h3>
          <p className="note-box">
            日线 {data.bars} 条：{data.data_start} — {data.data_cutoff}
            <br />
            分钟数据 {data.minute_bars} 条 · 模型版本 {data.models} 个 · 快照{" "}
            {data.snapshots} 个
          </p>
          <p className="section-note">
            当前运行数据目录：
            <span className="storage-path">{data.data_dir}</span>
            。GitHub更新同步代码，数据由完整备份保存。
          </p>
          <p>
            交易日历截至 {data.calendar_end}；当前数据之后待补的交易日{" "}
            {data.pending_open_day_count} 个，历史区间内缺失日线{" "}
            {data.missing_open_day_count} 个。
          </p>
          {(data.missing_open_day_count > 0 ||
            data.unknown_calendar_days > 0) && (
            <p className="note-box">
              缺失日期可能涉及停牌或数据遗漏，需要核对来源。
              {data.missing_open_days.join("、")} · 未知日历日期{" "}
              {data.unknown_calendar_days} 个
            </p>
          )}
          <p className="section-note">
            预测日历：下一交易日 {data.forecast_dates_supported["1"] || "不足"}
            ；20日 {data.forecast_dates_supported["20"] || "不足"}；60日{" "}
            {data.forecast_dates_supported["60"] || "不足"}
            。缺少日历时请导入交易所日历。
          </p>
          {data.raw_price_warning && (
            <p className="note-box">
              当前是不复权价格。分红、送转与除权可能影响长期走势，应使用可信复权数据重新训练；不同口径不会混用。
            </p>
          )}
          <h3>完整备份</h3>
          <p className="section-note">
            同时保存数据库、模型、预测部署产物、快照、原始上传与核对记录，并校验文件。任务进行中时暂不能备份。恢复会创建新的目录，保留现有数据。
          </p>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                const r = await post<any>("/maintenance/backups", {});
                setMessage(
                  `完整备份已校验：${r.verified_files}个文件。保存于 ${r.path}`,
                );
              })
            }
          >
            {busy ? "处理中…" : "创建并校验完整备份"}
          </button>
          <p className="section-note storage-path">
            备份目录：{data.backup_dir}
          </p>
          {data.backups.map((b: any) => (
            <div className="history-item" key={b.id}>
              <span>
                {b.created_at.slice(0, 16).replace("T", " ")} · {b.file_count}
                个文件
              </span>
              <button
                className="text-btn"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const r = await post<any>(
                      `/maintenance/backups/${b.id}/verify`,
                      {},
                    );
                    setMessage(`校验通过：${r.verified_files}个文件`);
                  })
                }
              >
                再次校验
              </button>
            </div>
          ))}
          <h3>第二来源行情核对</h3>
          <p className="section-note">
            上传同一价格口径的对照CSV/XLSX，含date/open/high/low/close/volume。只比较重合日期并保存差异，不替换行情。当前口径：
            {basis}。
          </p>
          <label>
            对照数据来源
            <input
              value={source}
              maxLength={200}
              onChange={(e) => setSource(e.target.value)}
              placeholder="例如另一供应商、下载日期与价格口径"
            />
          </label>
          <label>
            成交量单位
            <select
              value={volumeUnit}
              onChange={(e) => setVolumeUnit(e.target.value)}
            >
              <option value="shares">股</option>
              <option value="lots">手（每手100股）</option>
            </select>
          </label>
          <label>
            对照文件
            <input
              type="file"
              accept=".csv,.xlsx"
              onChange={(e) => setFile(e.target.files?.[0])}
            />
          </label>
          <button
            className="secondary"
            disabled={busy || !source.trim() || !file}
            onClick={() =>
              act(async () => {
                const body = new FormData();
                body.append("stock_id", stock!.id);
                body.append("price_basis", basis);
                body.append("source", source);
                body.append("volume_unit", volumeUnit);
                body.append("file", file!);
                const r = await api<any>("/maintenance/compare", {
                  method: "POST",
                  body,
                });
                setMessage(
                  `已核对${r.compared}个重合日期，一致${r.matching_days}个，存在差异${r.different_days}个`,
                );
              })
            }
          >
            核对并保存记录
          </button>
          {data.audits.length ? (
            data.audits.map((a: any) => (
              <details key={a.id}>
                <summary>
                  {a.source} · {a.compared}日 / {a.different_days}日存在差异
                </summary>
                <p className="section-note">{a.note}</p>
                <pre>{JSON.stringify(a.differences, null, 2)}</pre>
              </details>
            ))
          ) : (
            <p className="muted">尚无第二来源核对记录。</p>
          )}
        </>
      )}
      {message && (
        <p className="note-box storage-path" role="status">
          {message}
        </p>
      )}
      {(error || loadError) && <p className="error">{error || loadError}</p>}
    </Modal>
  );
}
