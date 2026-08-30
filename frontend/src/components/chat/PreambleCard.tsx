import { CheckCircleFilled, SearchOutlined } from "@ant-design/icons";
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
    <div className="fdp-preamble">
      <span className="fdp-preamble-icon"><SearchOutlined /></span>
      <div className="fdp-preamble-content">
        <div className="fdp-preamble-title">
          <strong>查询覆盖</strong>
          <span className={truncated ? "is-truncated" : ""}>
            <CheckCircleFilled /> {returned_count} / {code_count} 条
          </span>
          {skill_name && <em>{skill_name}</em>}
        </div>
        <code>{actual_query || JSON.stringify(args)}</code>
        {truncated && <small>结果较多，当前仅展示部分数据。</small>}
        {chunks.length > 0 && (
          <p><span>解析条件</span>{chunks.join(" · ")}</p>
        )}
      </div>
    </div>
  );
}
