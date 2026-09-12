/** 学生名单页：添加 / 改名 / 停用 / 批量粘贴导入（一行一条「学号,姓名」）。 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Student } from "../api/types";

/** 解析粘贴文本：每行「学号,姓名」或「学号 姓名」（支持中英文逗号 / 制表符）。 */
export function parseRosterText(text: string): { student_no: string; name: string }[] {
  const seen = new Set<string>();
  const out: { student_no: string; name: string }[] = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const parts = line.split(/[,，\t]/).length > 1
      ? line.split(/[,，\t]/)
      : line.split(/\s+/);
    const studentNo = (parts[0] ?? "").trim();
    const name = (parts[1] ?? "").trim();
    if (!studentNo || !name || seen.has(studentNo)) {
      continue;
    }
    seen.add(studentNo);
    out.push({ student_no: studentNo, name });
  }
  return out;
}

export default function StudentsPage() {
  const navigate = useNavigate();

  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [newNo, setNewNo] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  const [importText, setImportText] = useState("");
  const [showImport, setShowImport] = useState(false);

  const parsedImport = useMemo(() => parseRosterText(importText), [importText]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStudents(await api.listStudents());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "学生名单加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleAdd(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.createStudent(newNo.trim(), newName.trim());
      setNotice(`已添加 ${newName.trim()}`);
      setNewNo("");
      setNewName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "添加失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function handleRename(student: Student): Promise<void> {
    const next = window.prompt(`修改 ${student.name} 的姓名`, student.name);
    if (next === null || !next.trim() || next.trim() === student.name) {
      return;
    }
    setError("");
    try {
      await api.updateStudent(student.id, { name: next.trim() });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "修改失败，请重试");
    }
  }

  async function handleDeactivate(student: Student): Promise<void> {
    if (!window.confirm(`确定停用 ${student.name}（${student.student_no}）吗？停用后不再出现在名单，已有作文保留。`)) {
      return;
    }
    setError("");
    try {
      await api.deactivateStudent(student.id);
      setNotice(`${student.name} 已停用。`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "停用失败，请重试");
    }
  }

  async function handleImport(): Promise<void> {
    if (parsedImport.length === 0) {
      setError("没有可导入的行，请按「学号,姓名」每行一条填写");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.importStudents(parsedImport);
      setNotice(`导入完成：新增 ${result.created} 人，更新 ${result.updated} 人。`);
      setImportText("");
      setShowImport(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "导入失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            data-testid="back-home"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            期数列表
          </button>
          <h1 className="text-lg font-semibold text-slate-900">学生名单</h1>
        </div>
        <button
          type="button"
          onClick={() => setShowImport((value) => !value)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
        >
          {showImport ? "收起批量导入" : "批量导入"}
        </button>
      </header>

      {notice ? (
        <p className="mt-3 rounded-md bg-emerald-50 px-4 py-2 text-sm text-emerald-700">{notice}</p>
      ) : null}
      {error ? (
        <p className="mt-3 rounded-md bg-rose-50 px-4 py-2 text-sm text-rose-600">{error}</p>
      ) : null}

      {showImport ? (
        <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-sm font-medium text-slate-700">粘贴名单（每行一条：学号,姓名）</p>
          <textarea
            data-testid="import-text"
            className="mt-2 h-40 w-full resize-y rounded-lg border border-slate-300 p-3 font-mono text-sm outline-none focus:border-slate-500"
            placeholder={"20230101,张三\n20230102 李四"}
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
          />
          <div className="mt-2 flex items-center justify-between">
            <p className="text-xs text-slate-500">
              识别到 {parsedImport.length} 条；学号已存在的将更新姓名。
            </p>
            <button
              type="button"
              disabled={busy || parsedImport.length === 0}
              onClick={() => void handleImport()}
              className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-60"
            >
              {busy ? "导入中…" : "确认导入"}
            </button>
          </div>
        </section>
      ) : null}

      <form
        onSubmit={handleAdd}
        className="mt-4 flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-end"
      >
        <label className="flex-1 text-sm font-medium text-slate-700">
          学号
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
            value={newNo}
            onChange={(event) => setNewNo(event.target.value)}
            placeholder="如 20230101"
            required
          />
        </label>
        <label className="flex-1 text-sm font-medium text-slate-700">
          姓名
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-slate-500"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="如 张三"
            required
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          添加学生
        </button>
      </form>

      <section className="mt-5">
        {loading ? (
          <p className="text-sm text-slate-500">加载中…</p>
        ) : students.length === 0 ? (
          <p className="rounded-md bg-slate-100 px-4 py-3 text-sm text-slate-500">
            名单还是空的，先添加或批量导入学生。
          </p>
        ) : (
          <>
            <p className="text-sm text-slate-500">
              共 <span className="font-semibold text-slate-900">{students.length}</span> 名学生
            </p>
            <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {students.map((student) => (
                <li
                  key={student.id}
                  className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2"
                >
                  <span className="min-w-0">
                    <span className="text-sm font-medium text-slate-900">{student.name}</span>
                    <span className="ml-2 text-xs text-slate-400">{student.student_no}</span>
                  </span>
                  <span className="flex shrink-0 gap-1">
                    <button
                      type="button"
                      data-testid={"portfolio-link-" + student.id}
                      onClick={() => navigate("/students/" + student.id + "/portfolio")}
                      className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600"
                    >
                      成长档案
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleRename(student)}
                      className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600"
                    >
                      改名
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDeactivate(student)}
                      className="rounded border border-rose-200 px-2 py-1 text-xs text-rose-600"
                    >
                      停用
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
