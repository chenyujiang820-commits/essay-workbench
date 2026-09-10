/**
 * Vitest 全局测试设置。
 *
 * 项目使用 `globals: false`，@testing-library/react 无法自动注册 afterEach 清理，
 * 组件会跨用例残留于同一 jsdom document，导致 `getByTestId` 命中多个元素。
 * 此处显式注册 cleanup，保证每个用例后卸载已渲染组件。
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
