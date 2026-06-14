import { useState } from "react";
import { Collapse } from "antd";
import type { ToolCallRecord } from "../../stores/chatStore";

interface Props {
  records: ToolCallRecord[];
}

function asTable(data: unknown): { headers: string[]; rows: Array<Record<string, unknown>> } | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as Record<string, unknown>;
  const arr =
    (Array.isArray(obj.datas) && obj.datas) ||
    (Array.isArray(obj.articles) && obj.articles) ||
    (Array.isArray(obj.announcements) && obj.announcements) ||
    (Array.isArray(obj.reports) && obj.reports) ||
    null;
  if (!arr || arr.length === 0 || typeof arr[0] !== "object") return null;
  const headers = Object.keys(arr[0] as object);
  return { headers, rows: arr as Array<Record<string, unknown>> };
}

export function ToolCallCard({ records }: Props) {
  if (!records || records.length === 0) return null;
  return (
    <div style={{ margin: "8px 0" }}>
      {records.map((r, i) => (
        <ToolCallItem key={`${r.trace_id}-${i}`} record={r} />
      ))}
    </div>
  );
}

function ToolCallItem({ record }: { record: ToolCallRecord }) {
  const table = record.result ? asTable(record.result) : null;
  const status = record.ok === undefined ? "⏳" : record.ok ? "✅" : "❌";
  return (
    <div className={`fdp-tool-call ${record.ok === false ? "error" : ""}`}>
      <div>
        <strong>{status} {record.name}</strong>
        <span style={{ color: "#999", marginLeft: 8, fontSize: 11 }}>
          {record.duration_ms ? `${record.duration_ms}ms` : ""}
          {record.trace_id ? ` · ${record.trace_id.slice(0, 8)}` : ""}
        </span>
      </div>
      <div style={{ color: "#555", marginTop: 4 }}>
        <span className="fdp-step-tag">args</span>
        <code style={{ fontSize: 12 }}>{JSON.stringify(record.args)}</code>
      </div>
      {record.error && (
        <div style={{ color: "#cf1322", marginTop: 4 }}>⚠️ {record.error}</div>
      )}
      {table && (
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: "table",
              label: <span style={{ fontSize: 12 }}>📊 {table.rows.length} 行结果</span>,
              children: <ResultTable headers={table.headers} rows={table.rows.slice(0, 20)} />,
            },
          ]}
        />
      )}
    </div>
  );
}

function ResultTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: Array<Record<string, unknown>>;
}) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [asc, setAsc] = useState(true);
  const sorted = sortKey
    ? [...rows].sort((a, b) => {
        const av = a[sortKey] ?? "";
        const bv = b[sortKey] ?? "";
        return (asc ? 1 : -1) * String(av).localeCompare(String(bv), "zh");
      })
    : rows;
  return (
    <div style={{ overflowX: "auto", maxHeight: 320, overflowY: "auto" }}>
      <table className="fdp-tool-result-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th
                key={h}
                onClick={() => {
                  if (sortKey === h) setAsc(!asc);
                  else {
                    setSortKey(h);
                    setAsc(true);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                {h}
                {sortKey === h ? (asc ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={i}>
              {headers.map((h) => (
                <td key={h}>{String(r[h] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
