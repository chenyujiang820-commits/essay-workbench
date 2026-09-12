/** 与后端 app/schemas.py 对齐的 TypeScript 类型。 */

export interface Envelope<T> {
  code: number;
  data: T | null;
  message: string;
}

export interface LoginResult {
  token: string;
  token_type: string;
  expires_in: number;
}

export interface Student {
  id: number;
  student_no: string;
  name: string;
  active: number;
}

export type DiffType = "equal" | "replace" | "delete" | "insert";

export interface DiffSegment {
  type: DiffType;
  text_a: string;
  text_b: string;
}

export interface Photo {
  id: number;
  seq: number;
  file_path: string;
  width: number | null;
  height: number | null;
  /** 画质低于手写识别门槛（短边 <600 或长边 <800）：1 表示偏低。 */
  low_resolution: number;
  engine1_text: string | null;
  engine2_text: string | null;
  diff_json: DiffSegment[] | null;
}

export interface RecognitionTaskInfo {
  id: number;
  step: string;
  retry_count: number;
  error: string | null;
  updated_at: string;
}

export interface Issue {
  id: number;
  issue_no: number;
  week_start_date: string;
  created_at: string;
  essay_count: number;
}

/** 作文状态机：uploaded → recognizing → review → proofread | failed。 */
export type EssayStatus = "uploaded" | "recognizing" | "review" | "proofread" | "failed";

export interface EssaySummary {
  id: number;
  issue_id: number;
  student_id: number;
  student_name: string | null;
  title: string;
  status: string;
  low_confidence: number;
  /** 原片张数（看板一次性展示画质用，免逐篇点开）。 */
  photo_count: number;
  /** 画质偏低的原片张数。 */
  low_resolution_count: number;
  created_at: string;
  proofread_at: string | null;
  /** 教师评语（v1.3 / FR-04）：null 或空串都表示没有。 */
  teacher_comment: string | null;
  /** 评分 0~100（v1.3）：null = 未评分，不进任何榜。 */
  score: number | null;
  /** 星级 0~5，由后端按当前阈值算出 —— 前端不要再算第二遍。 */
  stars: number;
  /** 是否本期精选（1/0，v1.3 / FR-05）。 */
  selected: number;
}

export interface EssayDetail extends EssaySummary {
  final_text: string;
  photos: Photo[];
  task: RecognitionTaskInfo | null;
}

/** 应用元信息（免鉴权）。 */
export interface MetaInfo {
  class_name: string;
  version: string;
}

export interface UploadResult {
  essay_id: number;
  status: string;
  photo_count: number;
  task: RecognitionTaskInfo | null;
}

/** 处于「识别中」的状态集合（看板据此轮询）。 */
export const RECOGNIZING_STATUSES: ReadonlyArray<string> = ["uploaded", "recognizing"];

/** 判断作文是否仍在识别中（需要轮询）。 */
export function isRecognizing(status: string): boolean {
  return RECOGNIZING_STATUSES.includes(status);
}

// ---------------------------------------------------------------------------
// 成册导出 / 投屏（T04）
// ---------------------------------------------------------------------------
export interface TemplateInfo {
  key: string;
  name: string;
  description: string;
}

/** 成册排序方式：学号 / 姓名 / 佳作序（分数降序、未评分排最后。v1.3 放开 score）。 */
export type ExportOrder = "student_no" | "name" | "score";

export interface BookItem {
  student_no: string;
  name: string;
  title: string;
  paragraphs: string[];
  is_selected: boolean;
  /** 该篇尚未定稿、正文取的是识别初稿（仅投屏路径会为真，GAP-14）。 */
  is_draft?: boolean;
  /** 评语（v1.3 起可能非空）。 */
  comment?: string;
  score?: number | null;
  /** 星级（后端算好；0 表示未评分，模板与界面都不渲染）。 */
  stars?: number;
}

export interface PresentData {
  class_name: string;
  issue_no: number;
  week_start_date: string;
  generated_at: string;
  items: BookItem[];
  /** 未定稿（投屏显示的是识别初稿）的篇数。 */
  draft_count?: number;
  /** 既无定稿文字也无识别文字、因而没进轮播的篇数（0/undefined 表示没有排除）。 */
  excluded_no_text?: number;
}

// ---------------------------------------------------------------------------
// 二期（v1.3）：精选 / 三榜 / 成长档案 / 家长分享
// ---------------------------------------------------------------------------

/** 整期精选设置结果（覆盖式提交后回读的最终态）。 */
export interface SelectionResult {
  issue_id: number;
  selected_ids: number[];
  /** 每期精选上限（10）。 */
  limit: number;
  /** 建议预勾篇数（5），只是省点击。 */
  suggested: number;
  /** 本期已评分/未评分篇数（判据与三榜一致），勾完精选就地刷新提示条。 */
  scored_count: number;
  unscored_count: number;
}

export type BoardKey = "work" | "progress" | "star";

/** 佳作榜一行。 */
export interface WorkRow {
  rank: number;
  essay_id: number;
  student_id: number;
  student_no: string;
  name: string;
  title: string;
  score: number;
  stars: number;
  selected: boolean;
}

/** 进步榜一行（与上一有分期比较）。 */
export interface ProgressRow extends WorkRow {
  previous_score: number;
  previous_issue_no: number;
  delta: number;
}

/** 星级榜一行：后端不返回 rank 与 score，前端也就无从显示名次。 */
export interface StarRow {
  student_id: number;
  student_no: string;
  name: string;
  title: string;
  stars: number;
}

export interface BoardConfig {
  enabled: boolean;
  visibility: "teacher" | "public";
}

/** 三榜数据（含开关与可见性，来自 app.yaml）。 */
export interface RankingData {
  issue_id: number;
  issue_no: number;
  class_name: string;
  generated_at: string;
  thresholds: number[];
  max_stars: number;
  config: Record<BoardKey, BoardConfig>;
  work: WorkRow[];
  progress: ProgressRow[];
  star: StarRow[];
  /** 被配置关闭的榜：界面要显示"已关闭"，而不是"这周没人上榜"。 */
  disabled: BoardKey[];
  scored_count: number;
  unscored_count: number;
}

/** 成长档案的一条（一篇已定稿作文）。 */
export interface PortfolioEntry {
  essay_id: number;
  issue_id: number;
  issue_no: number;
  week_start_date: string;
  title: string;
  score: number | null;
  stars: number;
  selected: number;
  teacher_comment: string | null;
  proofread_at: string | null;
  photo_count: number;
}

export interface PortfolioStats {
  essay_count: number;
  selected_count: number;
  /** 上榜次数（佳作榜前 3 名落在该生身上的期数）。 */
  honoured_count: number;
  top_stars: number;
  avg_score: number | null;
  issue_count: number;
  last_issue_no: number | null;
}

export interface PortfolioData {
  student: Student;
  stats: PortfolioStats;
  entries: PortfolioEntry[];
  class_name: string;
  generated_at: string;
}

/** 家长分享链接（老师侧视图）。 */
export interface ShareLink {
  token: string;
  /** 站内相对路径；绝对 URL 由前端按当前域名拼（后端不知道自己的公网域名）。 */
  url: string;
  issue_id: number;
  issue_no: number;
  student_id: number | null;
  student_name: string | null;
  scope: "issue" | "student";
  created_at: string;
  expires_at: string;
  revoked: number;
  label: string;
  essay_count: number;
}

/** 家长只读视图（免鉴权接口返回）。 */
export interface ShareView {
  class_name: string;
  issue_no: number;
  week_start_date: string;
  generated_at: string;
  expires_at: string;
  scope: "issue" | "student";
  student_name: string | null;
  items: BookItem[];
}
