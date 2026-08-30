import { useState } from "react";
import { Collapse } from "antd";
import {
  CheckCircleFilled,
  CloseCircleFilled,
  DatabaseOutlined,
  LoadingOutlined,
} from "@ant-design/icons";
import type { ToolCallRecord } from "../../stores/chatStore";

interface Props {
  records: ToolCallRecord[];
}

function asTable(data: unknown): { headers: string[]; rows: Array<Record<string, unknown>> } | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as Record<string, unknown>;
  // ToolResult is wrapped by the backend as { tool, ok, data, ... }.
  // Accept the old direct shape too so persisted legacy messages remain
  // renderable after this contract repair.
  const payload = obj.data && typeof obj.data === "object"
    ? obj.data as Record<string, unknown>
    : obj;
  const arr =
    (Array.isArray(payload.datas) && payload.datas) ||
    (Array.isArray(payload.articles) && payload.articles) ||
    (Array.isArray(payload.announcements) && payload.announcements) ||
    (Array.isArray(payload.reports) && payload.reports) ||
    null;
  if (!arr || arr.length === 0 || typeof arr[0] !== "object") return null;
  const headers = Object.keys(arr[0] as object);
  return { headers, rows: arr as Array<Record<string, unknown>> };
}

export function ToolCallCard({ records }: Props) {
  if (!records || records.length === 0) return null;
  return (
    <section className="fdp-evidence-stack" aria-label="Skill 执行记录">
      <div className="fdp-evidence-stack-head">
        <span>证据收集</span>
        <small>{records.filter((record) => record.ok === true).length}/{records.length} 已完成</small>
      </div>
      {records.map((r, i) => (
        <ToolCallItem key={`${r.trace_id}-${i}`} record={r} />
      ))}
    </section>
  );
}

function ToolCallItem({ record }: { record: ToolCallRecord }) {
  const table = record.result ? asTable(record.result) : null;
  const pending = record.ok === undefined;
  const statusLabel = pending ? "执行中" : record.ok ? "已完成" : "失败";
  const statusIcon = pending
    ? <LoadingOutlined spin />
    : record.ok
      ? <CheckCircleFilled />
      : <CloseCircleFilled />;
  return (
    <div className={`fdp-tool-call ${record.ok === false ? "error" : ""} ${pending ? "is-pending" : ""}`}>
      <div className="fdp-tool-call-head">
        <span className="fdp-tool-icon"><DatabaseOutlined /></span>
        <div className="fdp-tool-identity">
          <strong>{record.name}</strong>
          <span>
            {record.trace_id ? `#${record.trace_id.slice(0, 8)}` : "金融数据能力"}
            {record.duration_ms ? ` · ${record.duration_ms}ms` : ""}
          </span>
        </div>
        <span className="fdp-tool-status">
          {statusIcon} {statusLabel}
        </span>
      </div>
      <div className="fdp-tool-query">
        <span>查询</span>
        <code>{String(record.args?.query ?? JSON.stringify(record.args))}</code>
      </div>
      {record.error && (
        <div className="fdp-tool-error">调用失败：{record.error}</div>
      )}
      {table && (
        <Collapse
          className="fdp-result-collapse"
          ghost
          size="small"
          items={[
            {
              key: "table",
              label: <span>查看 {table.rows.length} 条结构化结果</span>,
              children: <ResultTable headers={table.headers} rows={table.rows.slice(0, 20)} />,
            },
          ]}
        />
      )}
      {!pending && record.ok && !table && (
        <div className="fdp-tool-result-note">已取得非结构化证据，将在最终结论中引用。</div>
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
    <div className="fdp-result-table-wrap">
      <table className="fdp-tool-result-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>
                <button
                  type="button"
                  className="fdp-sort-button"
                  onClick={() => {
                    if (sortKey === h) setAsc(!asc);
                    else {
                      setSortKey(h);
                      setAsc(true);
                    }
                  }}
                >
                  {h}{sortKey === h ? (asc ? " ↑" : " ↓") : ""}
                </button>
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
