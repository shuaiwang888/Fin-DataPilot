import { useState } from "react";
import type { AnswerPresentation } from "../../lib/types";

const statusLabels = { ready: "数据已返回", partial: "部分证据缺失", empty: "证据不足" };

export function AnswerVisuals({ presentation }: { presentation: AnswerPresentation }) {
  const [chartId, setChartId] = useState("");
  const chart = presentation.charts.find((item) => item.id === chartId) ?? presentation.charts[0];
  const minimum = chart ? Math.min(0, ...chart.points.map((point) => point.value)) : 0;
  const maximum = chart ? Math.max(0, ...chart.points.map((point) => point.value)) : 0;
  const span = maximum - minimum || 1;
  const zero = -minimum / span * 100;
  return (
    <section className="fdp-answer-visuals" aria-label="研究数据概览">
      <div className={`fdp-answer-overview ${chart ? "has-chart" : ""}`}>
        <span className={`fdp-answer-quality is-${presentation.status}`}>{statusLabels[presentation.status]}</span>
        <span>成功查询 <strong>{presentation.successful_tools}/{presentation.total_tools}</strong></span>
        <span>可视化指标 <strong>{presentation.charts.length}</strong></span>
      </div>
      {presentation.warnings.length > 0 && (
        <ul className="fdp-answer-warnings">{presentation.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      )}
      {chart && (
        <figure className="fdp-answer-chart">
          <div className="fdp-chart-heading">
            <figcaption>{chart.title}{chart.unit && `（${chart.unit}）`}</figcaption>
            {presentation.charts.length > 1 && (
              <select aria-label="选择对比指标" value={chart.id} onChange={(event) => setChartId(event.target.value)}>
                {presentation.charts.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            )}
          </div>
          <ol className="fdp-chart-rows" aria-label={chart.title}>
            {chart.points.map((point) => {
              const position = (point.value - minimum) / span * 100;
              return (
                <li key={point.label}>
                  <span className="fdp-chart-label" title={point.label}>{point.label}</span>
                  <span className="fdp-chart-track" aria-hidden="true">
                    <span className="fdp-chart-zero" style={{ left: `${zero}%` }} />
                    <span className={`fdp-chart-bar ${point.value < 0 ? "is-negative" : ""}`} style={{ left: `${Math.min(position, zero)}%`, width: `${Math.abs(position - zero)}%` }} />
                  </span>
                  <strong>{point.value.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}{chart.unit}</strong>
                </li>
              );
            })}
          </ol>
          <p className="fdp-chart-source">来源：{chart.source}{chart.as_of ? ` · 查询时点：${chart.as_of}` : ""} · 展示 {chart.points.length}/{chart.total_rows} 条原始记录</p>
        </figure>
      )}
    </section>
  );
}
