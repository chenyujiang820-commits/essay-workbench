import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../api/client";
import { useAppStore } from "../store";

/** 口令登录页（适配手机）。 */
export default function LoginPage() {
  const navigate = useNavigate();
  const setToken = useAppStore((state) => state.setToken);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const result = await api.login(password);
      setToken(result.token);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold text-slate-900">班级作文工作台</h1>
        <p className="mt-2 text-sm text-slate-500">请输入教师口令登录</p>

        <label className="mt-6 block text-sm font-medium text-slate-700" htmlFor="password">
          口令
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-base outline-none focus:border-slate-500"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />

        {error ? <p className="mt-3 text-sm text-rose-600">{error}</p> : null}

        <button
          type="submit"
          disabled={loading}
          className="mt-6 w-full rounded-md bg-slate-900 px-4 py-2 text-base font-medium text-white disabled:opacity-60"
        >
          {loading ? "登录中…" : "登录"}
        </button>
      </form>
    </main>
  );
}
