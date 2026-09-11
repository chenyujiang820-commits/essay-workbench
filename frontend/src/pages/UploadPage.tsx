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
import { isLowResolution, processImages } from "../lib/image";

/** 安全创建 objectURL（jsdom / 隐私模式下可能不可用）。 */
function safeCreateObjectURL(file: File): string {
  try {
    return typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : "";
  } catch {
    return "";
  }
}

export default function UploadPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();

  const [issue, setIssue] = useState<Issue | null>(null);
  const [students, setStudents] = useState<Student[]>([]);
  const [studentId, setStudentId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [thumbs, setThumbs] = useState<string[]>([]);
  /** 与 files 同序的画质判定结果（true = 低于手写识别门槛）。 */
  const [lowRes, setLowRes] = useState<boolean[]>([]);
  const [compressing, setCompressing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
    setCompressing(true);
    try {
      // processImages 只解码一次就同时给出压缩产物与**原图**尺寸：画质必须按原图判，
      // 否则 4000×3000 的手写页被压到 2000×1500 后会被误判为偏低。
      const processed = await processImages(picked);
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
          学生
        </label>
        {students.length > 15 ? (
          <input
            type="search"
            aria-label="搜索学生"
            placeholder="输入姓名或学号过滤"
            className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            value={studentQuery}
            onChange={(event) => setStudentQuery(event.target.value)}
          />
        ) : null}
        <select
          id="student"
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
          value={studentId}
          onChange={(event) => setStudentId(event.target.value)}
        >
          <option value="">请选择学生</option>
          {filteredStudents.map((student) => (
            <option key={student.id} value={student.id}>
              {student.student_no} {student.name}
            </option>
          ))}
        </select>
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
