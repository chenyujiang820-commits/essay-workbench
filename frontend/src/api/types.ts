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
  created_at: string;
  proofread_at: string | null;
}

export interface EssayDetail extends EssaySummary {
  final_text: string;
  teacher_comment: string | null;
  score: number | null;
  selected: number;
  photos: Photo[];
  task: RecognitionTaskInfo | null;
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
