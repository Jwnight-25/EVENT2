import { useEffect, useRef, useState } from "react";
import { Newspaper, RefreshCw, ExternalLink } from "lucide-react";
import { api, post, type Stock } from "./api";
import { JobPanel, useLoad } from "./components";
const kinds: Record<string, string> = {
  news: "个股新闻",
  stock_report: "个股研报",
  industry_report: "行业研报",
};
const stamp = (value: string | null, dateOnly = false) =>
  value
    ? dateOnly
      ? new Date(value).toLocaleDateString("zh-CN", {
          timeZone: "Asia/Shanghai",
        })
      : new Date(value).toLocaleString("zh-CN", {
          timeZone: "Asia/Shanghai",
          hour12: false,
        })
    : "发布时间未知";
export function Intelligence({ stock }: { stock?: Stock }) {
  const [kind, setKind] = useState("");
  const [version, setVersion] = useState(0);
  const [job, setJob] = useState("");
  const [items, setItems] = useState<any[]>([]);
  const loadedMore = useRef(false);
  const scroll = useRef<HTMLDivElement>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const url = stock
    ? `/intelligence/${stock.id}?${new URLSearchParams(kind ? { kind } : {})}`
    : "";
  const activeUrl = useRef(url);
  activeUrl.current = url;
  const { data: response, error: loadError } = useLoad(
    async () => (url ? { url, payload: await api<any>(url) } : undefined),
    [url, version],
    true,
  );
  const data = response?.url === url ? response.payload : undefined;
  useEffect(() => {
    setItems([]);
    setCursor(null);
    loadedMore.current = false;
    setError("");
    setJob("");
    setBusy(false);
    scroll.current?.scrollTo({ top: 0 });
  }, [url]);
  useEffect(() => {
    if (data) {
      setItems((old) => [
        ...data.items,
        ...old.filter(
          (item) => !data.items.some((fresh: any) => fresh.id === item.id),
        ),
      ]);
      if (!loadedMore.current) setCursor(data.next_cursor);
    }
  }, [data]);
  useEffect(() => {
    const t = window.setInterval(() => setVersion((v) => v + 1), 60000);
    return () => window.clearInterval(t);
  }, []);
  async function more() {
    if (!cursor || !url) return;
    setBusy(true);
    try {
      const r = await api<any>(url + `&before=${encodeURIComponent(cursor)}`);
      if (activeUrl.current !== url) return;
      setItems((old) => [
        ...old,
        ...r.items.filter((item: any) => !old.some((i) => i.id === item.id)),
      ]);
      loadedMore.current = true;
      setCursor(r.next_cursor);
    } catch (e) {
      if (activeUrl.current === url) setError((e as Error).message);
    } finally {
      if (activeUrl.current === url) setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">INFORMATION & RESEARCH</span>
          <h1>信息情报</h1>
          <p>
            关注 {stock?.name || "股票"} 的新闻与行业研究，按发布时间滚动浏览。
          </p>
        </div>
        <button
          className="secondary"
          disabled={!stock || busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const r = await post<{ job_id: string }>(
                `/intelligence/${stock!.id}/refresh`,
                {},
              );
              if (activeUrl.current === url) setJob(r.job_id);
            } catch (e) {
              if (activeUrl.current === url) setError((e as Error).message);
            } finally {
              if (activeUrl.current === url) setBusy(false);
            }
          }}
        >
          <RefreshCw size={16} />
          刷新情报
        </button>
      </div>
      <section className="card">
        <div className="intel-filters">
          <button className={!kind ? "active" : ""} onClick={() => setKind("")}>
            全部
          </button>
          {Object.entries(kinds).map(([key, label]) => (
            <button
              key={key}
              className={kind === key ? "active" : ""}
              onClick={() => setKind(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="section-note">
          {data?.schedule || "后台定时更新"}
          （北京时间）。新闻仅标记关键词关联，不自动认定会影响股价；研报观点属于发布机构。
        </p>
        {job && <JobPanel id={job} finished={() => setVersion((v) => v + 1)} />}
        {(error || loadError) && <p className="error">{error || loadError}</p>}
        {data?.syncs.some((s: any) => s.status === "failed") && (
          <p className="note-box">
            部分公开来源暂时不可用，已保留历史情报；可在下方查看更新记录。
          </p>
        )}
        <div
          ref={scroll}
          className="intelligence-scroll"
          aria-label="股票新闻与报告滚动列表"
        >
          {items.map((item) => (
            <article key={item.id} className="intelligence-item">
              <div className="intel-meta">
                <span className="pill">{kinds[item.kind]}</span>
                <span>
                  {stamp(
                    item.published_at,
                    item.metadata.published_precision === "date",
                  )}
                </span>
                <span>{item.publisher}</span>
              </div>
              <h3>
                <a href={item.url} target="_blank" rel="noopener noreferrer">
                  {item.title}
                  <ExternalLink size={14} />
                </a>
              </h3>
              {item.summary && <p>{item.summary}</p>}
              <p className="section-note">
                {item.metadata.association}{" "}
                {item.metadata.industry && ` · ${item.metadata.industry}`} ·
                抓取于 {stamp(item.retrieved_at)}
              </p>
            </article>
          ))}
          {!items.length && (
            <div className="empty">
              <Newspaper size={28} />
              <h3>尚无可展示的情报</h3>
              <p>
                等待首次后台更新，或点击刷新。接口未返回内容时会记录真实状态。
              </p>
            </div>
          )}
          {cursor && (
            <button
              className="secondary intel-load-more"
              disabled={busy}
              onClick={more}
            >
              {busy ? "读取中…" : "继续浏览更早的信息"}
            </button>
          )}
        </div>
        <details>
          <summary>情报更新记录</summary>
          {data?.syncs.map((s: any, index: number) => (
            <p className="section-note" key={index}>
              {kinds[s.kind]} · {stamp(s.created_at)} ·{" "}
              {(
                {
                  succeeded: "已更新",
                  failed: "失败",
                  skipped: "未识别行业，暂跳过",
                  running: "更新中",
                } as Record<string, string>
              )[s.status] || s.status}{" "}
              · 新增 {s.result.added || 0} 条{" "}
              {s.error || s.result.evidence?.note || ""}
            </p>
          ))}
        </details>
      </section>
    </>
  );
}
