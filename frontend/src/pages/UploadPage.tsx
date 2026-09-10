/**
 * 拍照 / 多图上传：手机浏览器调用相机（capture=environment），一张或多张原片
 * 一次提交为一篇作文。上传前用 canvas 压缩到最长边 ≤2000px / 质量 0.85。
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Issue, Student, UploadResult } from "../api/types";
import { compressImages } from "../lib/image";

export default function UploadPage() {
  const { issueId } = useParams();
  const navigate = useNavigate();

  const [issue, setIssue] = useState<Issue | null>(null);
  const [students, setStudents] = useState<Student[]>([]);
  const [studentId, setStudentId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [compressing, setCompressing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);

  const numericIssueId = Number(issueId);

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
      const compressed = await compressImages(picked);
      setFiles((previous) => [...previous, ...compressed]);
    } finally {
      setCompressing(false);
    }
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
      setFiles([]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "上传失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">
          上传原片{issue ? ` · 第 ${issue.issue_no} 期` : ""}
        </h1>
        <button
          type="button"
          onClick={() => navigate(`/issues/${issueId}/essays`)}
          className="text-sm text-slate-500"
        >
          返回看板
        </button>
      </header>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
        <label className="block text-sm font-medium text-slate-700" htmlFor="student">
          学生
        </label>
        <select
          id="student"
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
          value={studentId}
          onChange={(event) => setStudentId(event.target.value)}
        >
          <option value="">请选择学生</option>
          {students.map((student) => (
            <option key={student.id} value={student.id}>
              {student.student_no} {student.name}
            </option>
          ))}
        </select>

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
          capture="environment"
          multiple
          className="sr-only"
          onChange={handleFiles}
        />

        {compressing ? <p className="mt-2 text-sm text-slate-500">压缩中…</p> : null}

        {files.length > 0 ? (
          <div className="mt-3">
            <p className="text-sm text-slate-600">已选 {files.length} 张：</p>
            <ul className="mt-1 flex flex-wrap gap-2">
              {files.map((file, index) => (
                <li
                  key={`${file.name}-${index}`}
                  className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-600"
                >
                  {index + 1}. {file.name}
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setFiles([])}
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
