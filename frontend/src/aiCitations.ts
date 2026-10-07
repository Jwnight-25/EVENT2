export type AIContentData = {
  text?: string;
  message?: string;
  sources?: {
    url: string;
    title: string;
    retrieved_at?: string;
    published_at?: string;
  }[];
  citations?: { end: number; url: string }[];
  web_searched?: boolean;
  generated_at?: string;
};
export function citationParts(content: AIContentData) {
  const chars = Array.from(content.text || content.message || "");
  const sources = (content.sources || []).filter((s) =>
    /^https?:\/\//.test(s.url),
  );
  const parts: { text: string; url?: string; label?: string }[] = [];
  let start = 0;
  for (const c of [...(content.citations || [])].sort(
    (a, b) => a.end - b.end,
  )) {
    const index = sources.findIndex((s) => s.url === c.url);
    if (
      index < 0 ||
      !Number.isInteger(c.end) ||
      c.end < start ||
      c.end > chars.length
    )
      continue;
    parts.push({
      text: chars.slice(start, c.end).join(""),
      url: c.url,
      label: `[${index + 1}]`,
    });
    start = c.end;
  }
  parts.push({ text: chars.slice(start).join("") });
  return { parts, sources };
}
