/** 浏览器下载工具：把 Blob 触发为文件下载（并在完成后回收 objectURL）。 */

export function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** 整册 PDF 下载文件名：第N期作文集.pdf。 */
export function bookFileName(issueNo: number): string {
  return `第${issueNo}期作文集.pdf`;
}

/**
 * 单篇版式下载文件名：第N期-姓名-标题.pdf。
 *
 * 兜底词用「未命名」而非「无题」：v1.2 FR-11 起标题由识别自动抽取、老师可改，
 * 空标题意味着"还没填"，用"无题"会被误读成学生真的写了篇无题作文。
 */
export function singleFileName(issueNo: number, name: string, title: string): string {
  const safeTitle = (title.trim() || "未命名").replace(/[\\/:*?"<>|]/g, "_");
  return `第${issueNo}期-${name}-${safeTitle}.pdf`;
}
