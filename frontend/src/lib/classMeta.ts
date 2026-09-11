/**
 * 班级名（v1.2 OPT-02）：Web 端顶栏展示，与成册 PDF 同源（数据目录 app.yaml 的 class_name）。
 *
 * 一期只在 PDF 里出现班级名，老师在网页上看不出自己管的是哪个班。这里做一次进程内
 * 缓存：`/api/meta` 免鉴权且几乎不变，多个页面各自拉一次没必要；失败时静默返回空串，
 * 由调用方决定不渲染——班级名属锦上添花，绝不能因为它拉不到而给老师弹错误。
 */

import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { MetaInfo } from "../api/types";

let cached: Promise<MetaInfo | null> | null = null;

/** 取班级名与版本（模块级缓存一次）。 */
export function loadMeta(): Promise<MetaInfo | null> {
  if (!cached) {
    cached = api.fetchMeta().catch(() => null);
  }
  return cached;
}

/** 测试用：清空缓存，避免用例间互相污染。 */
export function resetMetaCache(): void {
  cached = null;
}

/** 组件内读取班级名；未就绪或失败时为空串。 */
export function useClassName(): string {
  const [className, setClassName] = useState("");

  useEffect(() => {
    let cancelled = false;
    void loadMeta().then((meta) => {
      if (!cancelled && meta) {
        setClassName(meta.class_name);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return className;
}
