/**
 * 图片压缩：上传前在浏览器端用 canvas 缩放，最长边压到 {@link MAX_EDGE} px，
 * 并以 JPEG {@link JPEG_QUALITY} 质量重编码，显著降低手机原片体积与上传耗时。
 *
 * 非图片文件（或无法解码的图片）直接原样返回，保证流水线不被压缩环节阻断。
 */

export const MAX_EDGE = 2000;
export const JPEG_QUALITY = 0.85;

export interface CompressOptions {
  /** 最长边上限（px）。 */
  maxEdge?: number;
  /** JPEG 编码质量（0~1）。 */
  quality?: number;
}

/** 目标画布尺寸：等比缩放，且仅缩小不放大。 */
export function targetSize(
  width: number,
  height: number,
  maxEdge: number,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge || longest === 0) {
    return { width, height };
  }
  const scale = maxEdge / longest;
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

function toJpegName(name: string): string {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  return `${stem}.jpg`;
}

/** 解码图片：优先 createImageBitmap，回退到 <img> + objectURL。 */
async function loadImage(
  file: File,
): Promise<{ width: number; height: number; source: CanvasImageSource; revoke: () => void }> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    return { width: bitmap.width, height: bitmap.height, source: bitmap, revoke: () => bitmap.close() };
  }
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image();
      element.onload = () => resolve(element);
      element.onerror = () => reject(new Error("图片解码失败"));
      element.src = url;
    });
    return {
      width: image.naturalWidth,
      height: image.naturalHeight,
      source: image,
      revoke: () => URL.revokeObjectURL(url),
    };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

function canvasToBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
  });
}

/**
 * 压缩单张图片；非图片或压缩失败时返回原文件。
 *
 * @param file 待压缩文件。
 * @param options 压缩参数（默认最长边 2000px、质量 0.85）。
 * @returns 压缩后的 JPEG `File`，或原文件。
 */
export async function compressImage(file: File, options: CompressOptions = {}): Promise<File> {
  const maxEdge = options.maxEdge ?? MAX_EDGE;
  const quality = options.quality ?? JPEG_QUALITY;

  if (!file.type.startsWith("image/")) {
    return file; // 非图片直接短路
  }

  let decoded: Awaited<ReturnType<typeof loadImage>> | null = null;
  try {
    decoded = await loadImage(file);
    const { width, height } = targetSize(decoded.width, decoded.height, maxEdge);

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) {
      return file; // 无 2D 上下文（极端环境）则降级原图
    }
    context.drawImage(decoded.source, 0, 0, width, height);

    const blob = await canvasToBlob(canvas, quality);
    if (!blob) {
      return file;
    }
    const name = toJpegName(file.name || "photo.jpg");
    return new File([blob], name, { type: "image/jpeg", lastModified: file.lastModified });
  } catch {
    return file; // 解码/编码失败一律降级原图，不阻断上传
  } finally {
    decoded?.revoke();
  }
}

/** 批量压缩（保序）。 */
export async function compressImages(files: File[], options: CompressOptions = {}): Promise<File[]> {
  return Promise.all(files.map((file) => compressImage(file, options)));
}
