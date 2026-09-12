/**
 * 星级徽标：只做显示，不做换算。
 *
 * 星级一律由后端按 app.yaml 的阈值算好下发（``stars`` 字段），前端再算一遍就会和 PDF、
 * 家长页、投屏三处口径分叉 —— 一期画质判定（``low_resolution``）已经为此立过规矩。
 */

interface StarsProps {
  /** 0~5；0 表示未评分，整个组件不渲染任何内容（不留空位）。 */
  stars: number;
  /** 附带的分数（仅在老师可见的视图里传，星级榜不要传）。 */
  score?: number | null;
  max?: number;
  /** 无障碍标签：屏幕阅读器不会念星星符号。 */
  label?: string;
}

export default function Stars({ stars, score = null, max = 5, label }: StarsProps) {
  if (!stars || stars <= 0) {
    return null;
  }
  const filled = Math.min(stars, max);
  const text = label ?? `${filled} 星${score === null || score === undefined ? "" : `（${score} 分）`}`;
  return (
    <span
      className="stars"
      data-testid="stars"
      data-stars={filled}
      title={text}
      aria-label={text}
      role="img"
    >
      {"★".repeat(filled)}
      {"☆".repeat(Math.max(0, max - filled))}
      {score !== null && score !== undefined ? (
        <span className="stars-score" data-testid="stars-score">
          {score}
          {" 分"}
        </span>
      ) : null}
    </span>
  );
}
