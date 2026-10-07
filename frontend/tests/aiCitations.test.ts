import test from "node:test";
import assert from "node:assert/strict";
import { citationParts } from "../src/aiCitations.ts";

test("inline source links preserve Chinese and emoji at code-point citation offsets", () => {
  const text = "公告📈显示增长，模型仍有误差。";
  const result = citationParts({
    text,
    sources: [{ url: "https://exchange.example/report?id=1", title: "公告" }],
    citations: [{ end: 7, url: "https://exchange.example/report?id=1" }],
  });
  assert.equal(result.parts.map((p) => p.text).join(""), text);
  assert.equal(result.parts[0].text, "公告📈显示增长");
  assert.equal(result.parts[0].label, "[1]");
});

test("unsafe or missing citation sources never become links", () => {
  const result = citationParts({
    text: "test",
    sources: [{ url: "javascript:alert(1)", title: "bad" }],
    citations: [
      { end: 4, url: "javascript:alert(1)" },
      { end: 999, url: "https://example.com" },
    ],
  });
  assert.equal(result.sources.length, 0);
  assert.deepEqual(result.parts, [{ text: "test" }]);
});
