import { Tag } from "antd";
import type { AnswerPreamble } from "../../lib/types";

interface Props {
  preamble: AnswerPreamble;
}

export function PreambleCard({ preamble }: Props) {
  const { actual_query, code_count, returned_count, chunks_info, args, skill_name } = preamble;
  const truncated = code_count > returned_count;
  const chunks = Array.isArray(chunks_info)
    ? chunks_info
    : chunks_info
      ? [String(chunks_info)]
      : [];

  return (
    <div
      className="fdp-preamble"
      style={{
        background: "#f0f5ff",
        border: "1px solid #d6e4ff",
        borderRadius: 8,
        padding: "8px 12px",
        margin: "8px 0",
        fontSize: 12,
        color: "#333",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 6, marginBottom: chunks.length ? 4 : 0 }}>
        <span style={{ color: "#888" }}>🔎 实际查询：</span>
        <code style={{ background: "#fff", padding: "1px 6px", borderRadius: 4, color: "#1677ff" }}>
          {actual_query || JSON.stringify(args)}
        </code>
        <Tag color={truncated ? "orange" : "green"} style={{ marginLeft: 4 }}>
          {returned_count} / {code_count} 条
          {truncated && "（数据较多，仅展示前 N 条）"}
        </Tag>
        {skill_name && <Tag>{skill_name}</Tag>}
      </div>
      {chunks.length > 0 && (
        <div style={{ color: "#666" }}>
          <span style={{ color: "#888" }}>解析条件：</span>
          <code style={{ background: "#fff", padding: "1px 6px", borderRadius: 4, color: "#555" }}>
            {chunks.join(" · ")}
          </code>
        </div>
      )}
    </div>
  );
}
