import { useState } from "react";
import { Upload } from "lucide-react";
import { api } from "./api";
import { Modal } from "./components";

export function CalendarUpload({ close }: { close: () => void }) {
  const [file, setFile] = useState<File>();
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<any>();
  return (
    <Modal title="导入交易日历" close={close}>
      <p className="muted">
        内置历史日历截至2025年。请使用可靠来源的CSV/XLSX，包含范围内全部自然日；date为日期，is_open为0或1。不会用工作日代替交易日。
      </p>
      <label>
        日历来源
        <input
          value={source}
          required
          placeholder="交易所公告或数据来源说明"
          onChange={(e) => setSource(e.target.value)}
        />
      </label>
      <label className="dropzone">
        <Upload size={24} />
        <span>{file?.name || "选择日历文件"}</span>
        <input
          type="file"
          accept=".csv,.xlsx"
          onChange={(e) => setFile(e.target.files?.[0])}
        />
      </label>
      <button
        className="primary"
        disabled={!file || !source || busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            const form = new FormData();
            form.append("file", file!);
            form.append("source", source);
            setResult(
              await api("/calendar/import", { method: "POST", body: form }),
            );
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        确认导入日历
      </button>
      {result && (
        <p className="note-box">
          已导入 {result.count} 天：{result.start} 至 {result.end}
          。未来预测将使用该日历。
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </Modal>
  );
}
