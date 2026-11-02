import { CheckCircleFilled, SearchOutlined } from "@ant-design/icons";
import type { AnswerPreamble } from "../../lib/types";

interface Props {
  preamble: AnswerPreamble;
}

export function PreambleCard({ preamble }: Props) {
  const { actual_query, code_count, returned_count, args } = preamble;
  const truncated = code_count > returned_count;

  return (
    <div className="fdp-preamble">
      <span className="fdp-preamble-icon"><SearchOutlined /></span>
      <div className="fdp-preamble-content">
        <div className="fdp-preamble-title">
          <strong>查询覆盖</strong>
          <span className={truncated ? "is-truncated" : ""}>
            <CheckCircleFilled /> {code_count > 0 ? `${returned_count} / ${code_count}` : returned_count} 条
          </span>
        </div>
        <code>{actual_query || JSON.stringify(args)}</code>
        {truncated && <small>结果较多，当前仅展示部分数据。</small>}
      </div>
    </div>
  );
}
