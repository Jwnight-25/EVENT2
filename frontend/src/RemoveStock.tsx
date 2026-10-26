import { useState } from "react";
import { api, type Stock } from "./api";
import { Modal } from "./components";

export function RemoveStock({
  stock,
  close,
  removed,
}: {
  stock: Stock;
  close: () => void;
  removed: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      title="删除关注股票"
      close={() => {
        if (!busy) close();
      }}
    >
      <p>
        将{" "}
        <strong>
          {stock.name} · {stock.code}
        </strong>{" "}
        从股票选择列表移出？
      </p>
      <p className="section-note">
        行情、模型、预测和积分全部保留，可在“添加股票”中恢复。移出后停止后续自动调度，已提交的任务会继续完成。
      </p>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button className="secondary" disabled={busy} onClick={close}>
          取消移出
        </button>
        <button
          className="secondary danger"
          disabled={busy}
          onClick={async () => {
            setError("");
            setBusy(true);
            try {
              await api(`/stocks/${stock.id}`, { method: "DELETE" });
              removed(stock.id);
            } catch (e) {
              setError((e as Error).message);
              setBusy(false);
            }
          }}
        >
          {busy ? "正在移出…" : "确认移出关注"}
        </button>
      </div>
    </Modal>
  );
}
