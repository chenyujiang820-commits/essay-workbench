/**
 * 三榜表彰（v1.3 / FR-04，老师侧）。
 *
 * 三条约束：
 * 1. 星级一律显示后端下发的 stars，前端不再按分数换算 —— 与 PDF、家长页、投屏同源。
 * 2. 星级榜不显示名次与分数（弱化竞争），后端就不下发这两个字段。
 * 3. 被配置关闭的榜显示「已按配置关闭」而不是「没人上榜」。
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { BoardKey, RankingData } from "../api/types";
import Stars from "../components/Stars";
import { triggerDownload } from "../lib/download";

export const BOARD_TITLES: Record<BoardKey, string> = {
  work: "佳作榜",
  progress: "进步榜",
  star: "星级榜",
};

export const TOP_HIGHLIGHT = 3;

export function highlightClass(rank: number): string {
  return rank <= TOP_HIGHLIGHT ? "bg-amber-50/70" : "";
}

export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(Number(score))) {
    return "—";
  }
  return Number(score).toFixed(1).replace(/[.]0$/, "");
}

export function thresholdHint(thresholds: number[], maxStars: number): string {
  if (!thresholds || thresholds.length === 0) {
    return "星级未配置阈值，本期不评星。";
  }
  const parts = thresholds.map((value, index) => "≥" + value + " " + (index + 1) + "星");
  const extra = maxStars - thresholds.length;
  if (extra > 0) {
    parts.push("再高 " + extra + " 档至 " + maxStars + " 星");
  }
  return "星级阈值：" + parts.join("、") + "。";
}
