import { useEffect, useRef, useState } from "react";
import { MessageCircle, Plus, Send } from "lucide-react";
import { api, post, horizons, type Prediction, type Stock } from "./api";
import { Modal, JobPanel, useLoad } from "./components";
import { AISettingsForm, type AIConfig } from "./AISettings";
import { AIContent, type AIContentData } from "./AIContent";

type Preference = {
  field: string;
  label: string;
  value: string;
  source: string;
  quote: string;
  confirmed: boolean;
  locked: boolean;
};
type Conversation = {
  id: string;
  title: string;
  updated_at: string;
  messages: Message[];
  preferences: Preference[];
};
type Message = {
  id: string;
  role: string;
  text: string;
  prediction_id: string | null;
  job_id: string | null;
  job_status: string;
  job_error: string | null;
  created_at: string;
  metadata_json: AIContentData & {
    context?: {
      prediction?: {
        data_cutoff: string;
        horizon: string;
        prediction_id: string;
      };
      preferences: Preference[];
    };
    memory_warning?: string;
  };
};

export function AdviceDialog({
  stock,
  prediction,
  history,
  close,
}: {
  stock: Stock;
  prediction?: Prediction;
  history: Prediction[];
  close: () => void;
}) {
  const [version, setVersion] = useState(0);
  const [id, setId] = useState(
    localStorage.getItem("advice-" + stock.id) || "",
  );
  const [draft, setDraft] = useState("");
  const [contextId, setContextId] = useState(
    prediction?.result.models?.length ? prediction.id : "",
  );
  const [learn, setLearn] = useState(true);
  const [sending, setSending] = useState(false);
  const [job, setJob] = useState("");
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [edit, setEdit] = useState<{ field: string; value: string } | null>(
    null,
  );
  const requestKey = useRef("");
  const end = useRef<HTMLDivElement>(null);
  const refresh = () => setVersion((v) => v + 1);
  const { data: config } = useLoad(
    () => api<AIConfig>("/ai/settings"),
    [version],
  );
  const { data: list, error: listError } = useLoad(
    () => api<Conversation[]>("/advice/conversations?stock_id=" + stock.id),
    [stock.id, version],
    true,
  );
  const activeId =
    id && list?.some((c) => c.id === id) ? id : list?.[0]?.id || "";
  const {
    data: conversation,
    error: conversationError,
    loading,
  } = useLoad(
    () =>
      activeId
        ? api<Conversation>("/advice/conversations/" + activeId)
        : Promise.resolve(undefined),
    [activeId, version],
    true,
  );
  const { data: preferences, error: preferenceError } = useLoad(
    () => api<Preference[]>("/advice/preferences"),
    [version],
    true,
  );
  const visible = conversation?.id === activeId ? conversation : undefined;
  const pending = visible?.messages.find(
    (m) => m.role === "user" && ["queued", "running"].includes(m.job_status),
  );
  const waiting = sending || !!pending || !!job;
  const lastUser = visible?.messages.filter((m) => m.role === "user").at(-1);
  const [memoryNotice, setMemoryNotice] = useState("");
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [activeId, visible?.messages.length]);
  function select(value: string) {
    setId(value);
    localStorage.setItem("advice-" + stock.id, value);
    setJob("");
    setError("");
  }
  async function create() {
    const c = await post<Conversation>("/advice/conversations", {
      stock_id: stock.id,
    });
    select(c.id);
    refresh();
    return c.id;
  }
  async function action(operation: () => Promise<unknown>) {
    setError("");
    setSending(true);
    try {
      await operation();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  async function send() {
    if (!draft.trim() || waiting) return;
    setSending(true);
    setError("");
    requestKey.current ||= crypto.randomUUID();
    try {
      const conversationId = activeId || (await create());
      const r = await post<{ job_id: string }>(
        "/advice/conversations/" + conversationId + "/messages",
        {
          text: draft,
          client_key: requestKey.current,
          prediction_id: contextId || null,
          learn_preferences: learn,
        },
      );
      setJob(r.job_id);
      setDraft("");
      requestKey.current = "";
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  return (
    <Modal title="交易建议对话" close={close} className="advice-modal">
      <div className="advice-intro">
        <MessageCircle size={20} />
        <div>
          <strong>
            {stock.name} · {stock.code}
          </strong>
          <p>结合模型结果、联网资料和你的偏好，讨论买入、持有与卖出情景。</p>
        </div>
      </div>
      <div className="row advice-toolbar">
        <select
          aria-label="选择历史交易对话"
          value={activeId}
          onChange={(e) => select(e.target.value)}
        >
          {!list?.length && <option value="">新的对话</option>}
          {list?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <button
          className="secondary"
          disabled={sending}
          onClick={() => action(create)}
        >
          <Plus size={14} />
          新建对话
        </button>
        <button
          className="text-btn"
          onClick={() => setSettingsOpen(!settingsOpen)}
        >
          {settingsOpen ? "收起接入设置" : "AI接入设置"}
        </button>
      </div>
      {settingsOpen && <AISettingsForm saved={refresh} />}
      <div className="advice-context">
        <label>
          本次提问参考的预测
          <select
            value={contextId}
            onChange={(e) => {
              setContextId(e.target.value);
              requestKey.current = "";
            }}
            disabled={waiting}
            aria-label="对话参考预测"
          >
            <option value="">不附带模型预测 · 一般研究</option>
            {history
              .filter((p) => p.result.models?.length)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {horizons[p.horizon]} ·{" "}
                  {p.created_at.slice(0, 16).replace("T", " ")} ·{" "}
                  {{ raw: "不复权", qfq: "前复权", hfq: "后复权" }[
                    p.config.price_basis as "raw" | "qfq" | "hfq"
                  ] || "未记录口径"}
                </option>
              ))}
          </select>
        </label>
        <p className="section-note">
          {config?.chat_available
            ? config.web_search
              ? "已配置联网服务，发送后检索并附上来源。"
              : "已配置普通对话服务，当前不联网，回答以保存的数据和条件情景为基础。"
            : "AI尚未接入。请展开AI接入设置，填写服务、模型与密钥后发送。"}{" "}
          回答仅供研究辅助，系统不执行交易。
        </p>
      </div>
      <details className="preference-panel">
        <summary>我的交易偏好 · 查看、修改和忘记</summary>
        <p className="section-note">
          已确认偏好直接使用；从对话提取的偏好标为“待核对”，后续回答会作为暂定线索。手动保存会锁定该项，AI不能覆盖。删除对话会移除来自那段对话的偏好，手动保存的偏好仍保留。
        </p>
        {preferences?.map((p) => (
          <div className="preference-item" key={p.field}>
            <div className="row">
              <strong>{p.label}</strong>
              <span className="muted">
                {p.value ? (p.confirmed ? "已确认" : "待核对") : "未设置"}
                {p.locked ? " · 自动记忆已关闭" : ""}
              </span>
            </div>
            <p>{p.value || "尚不了解，不作推断"}</p>
            {p.quote && (
              <small className="muted">
                {p.source === "project_brief"
                  ? "来自此前项目要求"
                  : p.source === "manual"
                    ? "由你手动设置"
                    : "来自你的对话原话"}
                ：“{p.quote}”
              </small>
            )}
            <div className="row">
              <button
                className="text-btn"
                disabled={sending}
                onClick={() => setEdit({ field: p.field, value: p.value })}
              >
                修改 / 确认
              </button>
              <button
                className="text-btn"
                disabled={sending}
                onClick={() =>
                  action(async () => {
                    await post(`/advice/preferences/${p.field}/forget`, {});
                    setMemoryNotice(`${p.label}已忘记，并停止自动记忆该项。`);
                  })
                }
              >
                忘记此项
              </button>
              {p.locked && (
                <button
                  className="text-btn"
                  disabled={sending}
                  onClick={() =>
                    action(async () => {
                      await post(`/advice/preferences/${p.field}/resume`, {});
                      setMemoryNotice(`${p.label}已重新开启自动记忆。`);
                    })
                  }
                >
                  重新开启记忆
                </button>
              )}
            </div>
            {edit?.field === p.field && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void action(async () => {
                    await post("/advice/preferences", edit);
                    setEdit(null);
                    setMemoryNotice(`${p.label}已保存并锁定。`);
                  });
                }}
              >
                <label>
                  {p.label}
                  <textarea
                    value={edit.value}
                    maxLength={500}
                    onChange={(e) =>
                      setEdit({ ...edit, value: e.target.value })
                    }
                  />
                </label>
                <div className="row">
                  <button
                    className="primary"
                    disabled={sending || !edit.value.trim()}
                  >
                    保存此项
                  </button>
                  <button
                    type="button"
                    className="text-btn"
                    onClick={() => setEdit(null)}
                  >
                    取消
                  </button>
                </div>
              </form>
            )}
          </div>
        ))}
        {memoryNotice && <p role="status">{memoryNotice}</p>}
      </details>
      <div
        className="advice-messages"
        role="log"
        aria-label="交易对话记录"
        aria-live="polite"
      >
        {!visible?.messages.length && (
          <div className="advice-empty">
            <strong>从一个具体问题开始</strong>
            <p>
              例如：“我倾向持有三个月，这次预测下应重点观察什么？”或说明你的持仓成本、仓位和可承受波动。
            </p>
            <div className="row">
              {[
                "结合当前预测，分析买入、持有和卖出的条件。",
                "帮我梳理适合稳健持有的观察清单。",
              ].map((s) => (
                <button
                  className="secondary"
                  disabled={waiting}
                  key={s}
                  onClick={() => {
                    setDraft(s);
                    requestKey.current = "";
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {visible?.messages.map((m) => (
          <article className={`advice-message ${m.role}`} key={m.id}>
            <div className="row">
              <strong>{m.role === "user" ? "你" : "AI研究助手"}</strong>
              <small className="muted">
                {m.created_at.slice(0, 16).replace("T", " ")}
              </small>
            </div>
            {m.role === "user" ? (
              <p className="ai-answer">{m.text}</p>
            ) : (
              <>
                <small className="ai-evidence-label">
                  {m.metadata_json.web_searched
                    ? "已进行联网检索"
                    : "本次未联网"}
                  {m.metadata_json.context?.prediction
                    ? ` · 模型数据截至 ${m.metadata_json.context.prediction.data_cutoff}`
                    : " · 未附带模型预测"}
                </small>
                <AIContent content={{ ...m.metadata_json, text: m.text }} />
                {m.metadata_json.memory_warning && (
                  <p className="section-note">
                    {m.metadata_json.memory_warning}
                  </p>
                )}
                <details>
                  <summary>本次使用的偏好</summary>
                  {m.metadata_json.context?.preferences
                    .filter((p) => p.value)
                    .map((p) => (
                      <p key={p.field}>
                        {p.label}：{p.value}（
                        {p.confirmed ? "已确认" : "待核对"}）
                      </p>
                    ))}
                </details>
              </>
            )}
            {m.role === "user" &&
              ["failed", "cancelled"].includes(m.job_status) && (
                <div>
                  <p className="error">
                    {m.job_status === "cancelled"
                      ? "这次回答已取消"
                      : m.job_error || "AI回答失败"}
                  </p>
                  {m.id === lastUser?.id && (
                    <button
                      className="secondary"
                      disabled={waiting || !config?.chat_available}
                      onClick={() =>
                        action(async () => {
                          const r = await post<{ job_id: string }>(
                            `/advice/messages/${m.id}/retry`,
                            {},
                          );
                          setJob(r.job_id);
                        })
                      }
                    >
                      重试这次提问
                    </button>
                  )}
                </div>
              )}
          </article>
        ))}
        <div ref={end} />
      </div>
      {(job || pending?.job_id) && (
        <JobPanel
          id={job || pending!.job_id!}
          finished={() => {
            setJob("");
            refresh();
          }}
        />
      )}
      {(error || listError || conversationError || preferenceError) && (
        <p className="error" role="alert">
          {error || listError || conversationError || preferenceError}
        </p>
      )}
      <form
        className="advice-compose"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <label>
          你的问题
          <textarea
            value={draft}
            maxLength={4000}
            disabled={waiting}
            placeholder="输入你的想法或具体问题…"
            onChange={(e) => {
              setDraft(e.target.value);
              requestKey.current = "";
            }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                void send();
              }
            }}
          />
        </label>
        <div className="row">
          <label className="inline-check">
            <input
              type="checkbox"
              checked={learn}
              disabled={waiting}
              onChange={(e) => {
                setLearn(e.target.checked);
                requestKey.current = "";
              }}
            />
            从本次原话总结长期偏好
          </label>
          <button
            className="primary"
            disabled={
              waiting || loading || !draft.trim() || !config?.chat_available
            }
          >
            <Send size={15} />
            {waiting ? "等待回答…" : "发送问题"}
          </button>
        </div>
      </form>
      {activeId && (
        <details className="forget-conversation">
          <summary>对话管理</summary>
          <p className="section-note">
            删除本段对话及从中自动记住的偏好。此前的完整备份仍可能包含旧记录。
          </p>
          <button
            className="text-btn"
            disabled={waiting}
            onClick={() =>
              action(async () => {
                await post(`/advice/conversations/${activeId}/forget`, {});
                select("");
              })
            }
          >
            删除本段对话和对应记忆
          </button>
        </details>
      )}
    </Modal>
  );
}
