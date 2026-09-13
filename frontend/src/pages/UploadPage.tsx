/**
 * 拍照 / 多图上传：一张或多张原片一次提交为一篇作文。上传前用 canvas 压缩到
 * 最长边 ≤2000px / 质量 0.85，并在选图当场判定画质是否低于手写识别门槛。
 *
 * v1.2 增补：
 * * FR-10 / GAP-02「拍摄要点」引导卡（正光 / 垂直 / 铺满 / 分辨率下限）；
 * * 低画质原片打角标 + 汇总提示，**只提醒不阻断**（老师可能只有这一张）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Issue, Student, UploadResult } from "../api/types";
import { useClassName } from "../lib/classMeta";
import {
  isAcceptedImage,
  isLowResolution,
  listRejectedImages,
  processImages,
} from "../lib/image";

/** 安全创建 objectURL（jsdom / 隐私模式下可能不可用）。 */
function safeCreateObjectURL(file: File): string {
  try {
    return typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : "";
  } catch {
    return "";
  }
}

/** 格式提示：点名前两张 + 总数，并给可执行的改法（手机相机常见 HEIC）。 */
function formatWarningText(names: string[]): string {
  const head =
    names.length > 2
      ? names.slice(0, 2).join("、") + " 等 " + names.length + " 张"
      : names.join("、");
  return (
    head +
    " 是手机相机常用但后端不接收的格式（多为 iPhone HEIC）。" +
    "请在手机「设置 → 相机 → 格式」选「兼容性（JPEG）」后重拍；其余照片已正常加入。"
  );
}

export default function UploadPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();

  const [issue, setIssue] = useState<Issue | null>(null);
  const [students, setStudents] = useState<Student[]>([]);
  const [studentId, setStudentId] = useState("");
  const [selectedStudentIds, setSelectedStudentIds] = useState<number[]>([]);
  const [uploadedStudentIds, setUploadedStudentIds] = useState<number[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [thumbs, setThumbs] = useState<string[]>([]);
  /** 与 files 同序的画质判定结果（true = 低于手写识别门槛）。 */
  const [lowRes, setLowRes] = useState<boolean[]>([]);
  const [compressing, setCompressing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /** 后端白名单外的照片（如 iPhone HEIC）文件名，选图当场提示改法。 */
  const [rejectedNames, setRejectedNames] = useState<string[]>([]);
  const [result, setResult] = useState<UploadResult | null>(null);
  /** 学生搜索（名单较长时显示）。 */
  const [studentQuery, setStudentQuery] = useState("");

  /** 卸载时回收全部 objectURL。 */
  const thumbsRef = useRef<string[]>([]);
  thumbsRef.current = thumbs;
  useEffect(
    () => () => {
      thumbsRef.current.forEach((url) => url && URL.revokeObjectURL(url));
    },
    [],
  );

  const numericIssueId = Number(issueId);
  const className = useClassName();

  const load = useCallback(async () => {
    setError("");
    try {
      const [issueData, studentList] = await Promise.all([
        api.getIssue(numericIssueId),
        api.listStudents(),
      ]);
      setIssue(issueData);
      setStudents(studentList);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载失败，请检查网络");
    }
  }, [numericIssueId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleFiles(event: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const picked = Array.from(event.target.files ?? []);
    // 允许同一文件二次选择
    event.target.value = "";
    if (picked.length === 0) {
      return;
    }
    setError("");
    // 手机相册里的原片可能是后端白名单外的格式（iPhone 默认 HEIC，浏览器压不动时
    // 只能降级上传原文件）。当场挑出来并给出可执行的改法，比等点提交后收到一句 400
    // 好得多；其余能收的照片照常加入，不让一整批都白选。
    setRejectedNames(listRejectedImages(picked));
    const usable = picked.filter((file) => isAcceptedImage(file));
    if (usable.length === 0) {
      return;
    }
    setCompressing(true);
    try {
      // processImages 只解码一次就同时给出压缩产物与**原图**尺寸：画质必须按原图判，
      // 否则 4000×3000 的手写页被压到 2000×1500 后会被误判为偏低。
      const processed = await processImages(usable);
      setFiles((previous) => [...previous, ...processed.map((item) => item.file)]);
      setThumbs((previous) => [...previous, ...processed.map((item) => safeCreateObjectURL(item.file))]);
      setLowRes((previous) => [
        ...previous,
        ...processed.map((item) => isLowResolution(item.width, item.height)),
      ]);
    } catch {
      setError("图片处理失败，请重试或换一张照片");
    } finally {
      setCompressing(false);
    }
  }

  function revokeThumb(url: string | undefined): void {
    if (url) {
      URL.revokeObjectURL(url);
    }
  }

  function removeFile(index: number): void {
    setThumbs((previous) => {
      revokeThumb(previous[index]);
      return previous.filter((_, position) => position !== index);
    });
    setFiles((previous) => previous.filter((_, position) => position !== index));
    setLowRes((previous) => previous.filter((_, position) => position !== index));
  }

  function clearFiles(): void {
    setThumbs((previous) => {
      previous.forEach(revokeThumb);
      return [];
    });
    setFiles([]);
    setLowRes([]);
  }

  async function handleSubmit(): Promise<void> {
    if (!studentId) {
      setError("请先选择学生");
      return;
    }
    if (files.length === 0) {
      setError("请至少拍摄或选择一张原片");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const uploaded = await api.uploadEssay(numericIssueId, Number(studentId), files);
      setResult(uploaded);
      clearFiles();
      setUploadedStudentIds((previous) => [...previous, Number(studentId)]);
      const nextStudent = selectedStudentIds.find(
        (id) => id !== Number(studentId) && !uploadedStudentIds.includes(id),
      );
      if (nextStudent !== undefined) {
        setStudentId(String(nextStudent));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "上传失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  const query = studentQuery.trim();
  const filteredStudents =
    query === ""
      ? students
      : students.filter(
          (student) => student.name.includes(query) || student.student_no.includes(query),
        );
  const studentSelectedHidden =
    studentId !== "" && !filteredStudents.some((student) => String(student.id) === studentId);
  const lowResCount = lowRes.filter(Boolean).length;
  const activeStudent = students.find((student) => String(student.id) === studentId);
  const pendingQueueCount = selectedStudentIds.filter(
    (id) => !uploadedStudentIds.includes(id),
  ).length;

  return (
    <main className="mx-auto max-w-2xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-slate-900">
          上传原片{issue ? ` · 第 ${issue.issue_no} 期` : ""}
          {className ? (
            <span className="ml-2 text-sm font-normal text-slate-500">{className}</span>
          ) : null}
        </h1>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => navigate("/")}
            data-testid="back-home"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            期数列表
          </button>
          <button
            type="button"
            onClick={() => navigate(`/issues/${issueId}/essays`)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            ← 看板
          </button>
        </div>
      </header>

      <section
        data-testid="capture-guide"
        className="mt-6 flex flex-col rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-slate-700"
      >
        {/* 拍摄引导直接决定识别率：斜拍/背光/低分辨率会让视觉模型更容易"编字"（PRD 附录A 实测）。
            桌面端可折叠省屏；手机首屏虽紧，但这几句更不能藏起来，故折叠仅在 lg 生效。 */}
        <summary className="-mx-1 cursor-pointer select-none px-1 font-medium text-sky-900">
          拍摄要点（直接影响识别准确率）
        </summary>
        <ul className="mt-2 space-y-1 text-xs leading-6 text-slate-600 sm:text-sm">
          <li>1. 光线要正：别背光，也别让手或手机在纸上留阴影。</li>
          <li>2. 本子放平，手机垂直俯拍，不要斜着拍。</li>
          <li>3. 字铺满画面，一页拍一张，不要把两页挤进一张。</li>
          <li>4. 照片短边不低于 600、长边不低于 800 像素，太模糊识别错字会明显变多。</li>
        </ul>
      </section>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <label className="block text-sm font-medium text-slate-700" htmlFor="student">
          选择学生
        </label>
        <input
          type="search"
          aria-label="搜索学生"
          placeholder="输入姓名或学号过滤"
          className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
          value={studentQuery}
          onChange={(event) => setStudentQuery(event.target.value)}
        />
        <select
          id="student"
          aria-label="学生"
          className="sr-only"
          value={studentId}
          onChange={(event) => {
            const value = event.target.value;
            setStudentId(value);
            setSelectedStudentIds(value ? [Number(value)] : []);
            setUploadedStudentIds([]);
          }}
        >
          <option value="">请选择学生</option>
          {filteredStudents.map((student) => (
            <option key={student.id} value={student.id}>
              {student.student_no} {student.name}
            </option>
          ))}
        </select>
        <div
          data-testid="student-grid"
          className="mt-3 grid max-h-64 grid-cols-4 gap-2 overflow-y-auto pr-1 min-[390px]:grid-cols-5"
        >
          {filteredStudents.map((student) => {
            const selected = selectedStudentIds.includes(student.id);
            const uploaded = uploadedStudentIds.includes(student.id);
            return (
              <label
                key={student.id}
                data-testid={`student-card-${student.id}`}
                className={`relative flex min-h-16 cursor-pointer flex-col justify-center rounded-md border px-2 py-2 text-left text-xs transition-colors ${
                  selected ? "border-slate-800 bg-slate-100" : "border-slate-200 bg-white"
                } ${uploaded ? "opacity-50" : ""}`}
              >
                <input
                  type="checkbox"
                  aria-label={`选择${student.name}`}
                  className="absolute right-1.5 top-1.5 h-3.5 w-3.5"
                  checked={selected}
                  disabled={uploaded}
                  onChange={() => {
                    setSelectedStudentIds((previous) => {
                      const next = selected
                        ? previous.filter((id) => id !== student.id)
                        : [...previous, student.id];
                      const active = next.find((id) => !uploadedStudentIds.includes(id));
                      setStudentId(active === undefined ? "" : String(active));
                      return next;
                    });
                  }}
                />
                <span className="pr-4 font-medium text-slate-900">{student.name}</span>
                <span className="mt-0.5 text-[10px] text-slate-400">{student.student_no}</span>
                {uploaded ? <span className="mt-0.5 text-[10px] text-emerald-600">已上传</span> : null}
              </label>
            );
          })}
        </div>
        <p data-testid="upload-queue" className="mt-2 text-xs text-slate-500">
          {pendingQueueCount > 0
            ? `已选 ${selectedStudentIds.length} 人，当前为 ${activeStudent?.name ?? "未选择"} 拍照；还需上传 ${pendingQueueCount} 人。`
            : "请选择需要拍照的学生，可连续处理多名学生。"}
        </p>
        {studentSelectedHidden ? (
          <p className="mt-1 text-xs text-slate-400">已选学生被搜索条件过滤，清空搜索框即可恢复显示。</p>
        ) : null}

        <label
          htmlFor="photo-input"
          className="mt-5 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center"
        >
          <span className="text-base font-medium text-slate-700">拍照 / 选择原片</span>
          <span className="text-xs text-slate-500">支持多张，将自动压缩后上传为同一篇</span>
        </label>
        <input
          id="photo-input"
          data-testid="photo-input"
          type="file"
          accept="image/*"
          multiple
          className="sr-only"
          onChange={handleFiles}
        />

        {rejectedNames.length > 0 ? (
          <p
            data-testid="format-warning"
            className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800"
          >
            {formatWarningText(rejectedNames)}
          </p>
        ) : null}

        {compressing ? <p className="mt-2 text-sm text-slate-500">压缩中…</p> : null}

        {files.length > 0 && lowResCount > 0 ? (
          <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {lowResCount} 张画质偏低，识别准确率会下降，建议重拍（仍可继续上传）。
          </p>
        ) : null}

        {files.length > 0 ? (
          <div className="mt-3">
            <p className="text-sm text-slate-600">已选 {files.length} 张：</p>
            <ul className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-4">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`} className="relative">
                  {thumbs[index] ? (
                    <img
                      src={thumbs[index]}
                      alt={`已选第 ${index + 1} 张`}
                      className="h-20 w-full rounded-md border border-slate-200 object-cover"
                    />
                  ) : (
                    <div className="flex h-20 items-center justify-center rounded-md bg-slate-100 px-1 text-center text-xs text-slate-500">
                      {file.name.slice(0, 12)}
                    </div>
                  )}
                  {lowRes[index] ? (
                    <span
                      data-testid="low-res-badge"
                      title="短边不足 600 或长边不足 800 像素，识别准确率会下降，建议重拍"
                      className="absolute bottom-1 left-0 rounded bg-amber-500 px-1.5 py-0.5 text-[10px] font-medium text-white shadow-sm"
                    >
                      画质偏低
                    </span>
                  ) : null}
                  <button
                    type="button"
                    aria-label={`删除第 ${index + 1} 张`}
                    onClick={() => removeFile(index)}
                    className="absolute -right-1.5 -top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-xs font-medium text-white"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={clearFiles}
              className="mt-2 text-xs text-rose-600"
            >
              清空
            </button>
          </div>
        ) : null}

        {error ? (
          <p className="mt-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-600">{error}</p>
        ) : null}

        {result ? (
          <div className="mt-4 rounded-md bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            已上传识别中（{result.photo_count} 张）。可继续上传其他学生，
            或
            <button
              type="button"
              className="ml-1 underline"
              onClick={() => navigate(`/issues/${issueId}/essays`)}
            >
              返回看板
            </button>
            查看进度。
          </div>
        ) : null}

        <button
          type="button"
          disabled={busy || compressing}
          onClick={handleSubmit}
          className="mt-5 w-full rounded-md bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-60"
        >
          {busy ? "上传中…" : "上传并识别"}
        </button>
      </section>
    </main>
  );
}
