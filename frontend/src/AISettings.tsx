import { useState } from "react";
import { api, post } from "./api";
import { useLoad } from "./components";

export type AIConfig = {
  provider: string;
  base_url: string;
  model: string;
  key_present: boolean;
  configured: boolean;
  web_search: boolean;
  explanation_available: boolean;
  chat_available: boolean;
};
export function AISettingsForm({ saved }: { saved: () => void }) {
  const [revision, setRevision] = useState(0);
  const { data, error: loadError } = useLoad(
    () => api<AIConfig>("/ai/settings"),
    [revision],
  );
  const [draft, setDraft] = useState<Partial<AIConfig>>({});
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const provider =
    draft.provider ??
    (data?.provider === "compatible" ? "compatible" : "openai");
  const base =
    draft.base_url ?? (data?.provider === "compatible" ? data.base_url : "");
  const model = draft.model ?? data?.model ?? "";
  const web = draft.web_search ?? (data?.configured ? data.web_search : true);
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await post("/ai/settings", {
        provider,
        base_url: provider === "openai" ? "https://api.openai.com/v1" : base,
        model,
        api_key: key || null,
        web_search: provider === "openai" && web,
      });
      setKey("");
      setRevision((v) => v + 1);
      setNotice("配置已保存在本机；尚未验证实际连接。");
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const r = await post<{ model: string }>("/ai/test", {});
      setNotice(
        `实际连接成功 · ${r.model || "服务已响应"}。本次仅测试对话，联网能力需在解释时验证。`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="ai-settings">
      <p className="section-note">
        密钥仅保存在本机，不回显、不上传GitHub。点击解释或发送对话时，会向你配置的服务发送当前预测、最近最多24条对话及已记住的偏好；过长的旧回答会缩略，服务可能产生费用。
      </p>
      <label>
        AI服务
        <select
          value={provider}
          onChange={(e) =>
            setDraft({
              provider: e.target.value,
              model: "",
              base_url: "",
              web_search: true,
            })
          }
        >
          <option value="openai">OpenAI · 支持联网检索</option>
          <option value="compatible">兼容OpenAI的服务 · 普通对话</option>
        </select>
      </label>
      {provider === "compatible" && (
        <label>
          服务地址
          <input
            value={base}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })}
            placeholder="https://服务地址/v1"
          />
        </label>
      )}
      <label>
        模型名称
        <input
          value={model}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          placeholder="填写服务账户可用的模型名称"
        />
      </label>
      <label>
        API密钥
        <input
          type="password"
          autoComplete="off"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={
            data?.key_present
              ? "已保存；同一服务地址留空可保留"
              : "在此处填写本机密钥"
          }
        />
      </label>
      {provider === "openai" ? (
        <label className="inline-check">
          <input
            type="checkbox"
            checked={web}
            onChange={(e) =>
              setDraft({ ...draft, web_search: e.target.checked })
            }
          />
          解释和建议使用联网检索并展示来源
        </label>
      ) : (
        <p className="section-note">
          兼容接口不启用联网工具，只能讨论已导入数据和条件情景。不会声称掌握当前行情。
        </p>
      )}
      <div className="row">
        <button
          className="primary"
          disabled={
            busy || !model.trim() || (provider === "compatible" && !base.trim())
          }
          onClick={save}
        >
          {busy ? "处理中…" : "保存AI配置"}
        </button>
        <button className="secondary" disabled={busy} onClick={test}>
          测试已保存的连接
        </button>
      </div>
      {(error || loadError) && (
        <p className="error" role="alert">
          {error || loadError}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <p className="section-note">
        完整备份保存对话与偏好，不包含此处的密钥文件。换电脑或恢复到新目录时需重新填写密钥。
      </p>
    </div>
  );
}
