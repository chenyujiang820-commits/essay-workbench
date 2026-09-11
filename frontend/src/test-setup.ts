/**
 * Vitest 全局测试设置。
 *
 * 1. 显式注册 cleanup：项目使用 `globals: false`，@testing-library/react 无法自动注册
 *    afterEach 清理，组件会跨用例残留于同一 jsdom document，导致 `getByTestId` 命中多个元素。
 * 2. 安装 jsdom / undici `AbortSignal` 兼容垫片（见下方注释），否则任何 react-router
 *    数据路由跳转在测试环境里都会抛未捕获的 `TypeError`，导航类用例全红。
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

/**
 * 【垫片】jsdom 的 `AbortSignal` 与 Node 内建 `fetch`/`Request`（undici）不属于同一实现。
 *
 * 现象：`main.tsx` 迁移到 `createBrowserRouter` 之后，任何 SPA 内跳转（含 `useBlocker`
 * 放行后的 `proceed()`）都会走到 `@remix-run/router` 的 `createClientSideRequest`，它执行
 * `new Request(url, { signal })`。此处 `signal` 由 jsdom 的 `AbortController` 产生，而测试
 * 环境里的 `Request` 是 Node 原生实现，它用自己的 `AbortSignal` 做 `instanceof` 校验：
 *
 *   TypeError: RequestInit: Expected signal ("AbortSignal {}") to be an instance of AbortSignal.
 *
 * 该异常在 `startNavigation` 内以 rejection 形式冒出，测试里表现为「点了不跳转」+
 * Unhandled Rejection。真实浏览器不受影响（fetch 家族同源），属纯测试环境问题。
 *
 * 处理：包装一层 `Request`，把不被接受的 `signal` 换成 `null`（规范允许，`Request.signal`
 * 会自动生成一个原生 signal）。本项目的路由不声明 loader/action，跳转中止链路不参与任何
 * 断言，因此剥离 `signal` 不损失被测行为。
 *
 * ⚠️ 请勿删除：删除后 EssayListPage / ProofreadPage / StudentsPage 的导航用例即刻转红。
 * 探测用「真的构造一次」而非比对构造函数——后者在本环境会得出假阳性结论。
 */
function installRequestAbortSignalShim(): void {
  const NativeRequest = globalThis.Request;
  if (typeof NativeRequest !== "function") {
    return;
  }

  let broken = false;
  try {
    new NativeRequest("http://ewb-shim.local/", { signal: new AbortController().signal });
  } catch {
    broken = true;
  }
  if (!broken) {
    return;
  }

  class CompatRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      const nextInit =
        init && typeof init === "object" && init.signal != null ? { ...init, signal: null } : init;
      super(input, nextInit);
    }
  }

  Object.defineProperty(globalThis, "Request", {
    value: CompatRequest,
    writable: true,
    configurable: true,
  });
  if (typeof window !== "undefined") {
    Object.defineProperty(window, "Request", {
      value: CompatRequest,
      writable: true,
      configurable: true,
    });
  }
}

installRequestAbortSignalShim();
