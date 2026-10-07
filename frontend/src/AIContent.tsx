import { Fragment } from "react";

import { citationParts, type AIContentData } from "./aiCitations";
export type { AIContentData } from "./aiCitations";

export function AIContent({ content }: { content: AIContentData }) {
  const { parts, sources } = citationParts(content);
  return (
    <div className="ai-content">
      <p className="ai-answer">
        {parts.map((p, i) => (
          <Fragment key={i}>
            {p.text}
            {p.url && (
              <a
                href={p.url}
                target="_blank"
                rel="noreferrer"
                aria-label={`来源${p.label}`}
              >
                {p.label}
              </a>
            )}
          </Fragment>
        ))}
      </p>
      {!!sources.length && (
        <div className="sources">
          {sources.map((s, i) => (
            <div key={s.url}>
              <a href={s.url} target="_blank" rel="noreferrer">
                [{i + 1}] {s.title}
              </a>
              <small className="muted">
                发布时间：{s.published_at || "未提供"} · 检索：
                {s.retrieved_at?.slice(0, 19).replace("T", " ") || "未记录"}
              </small>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
