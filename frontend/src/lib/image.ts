/**
 * 图片压缩：上传前在浏览器端用 canvas 缩放，最长边压到 {@link MAX_EDGE} px，
 * 并以 JPEG {@link JPEG_QUALITY} 质量重编码，显著降低手机原片体积与上传耗时。
 *
 * 非图片文件（或无法解码的图片）直接原样返回，保证流水线不被压缩环节阻断。
 *
 * 画质门槛（isLowResolution）口径与后端 app/images.py 完全一致：短边 <600 或
 * 长边 <800 视为偏低。手写识别依赖笔画细节，PRD v1.1 FR-01 引用了「800×600 以上」
 * 的接口要求，因此选图当场就要提示重拍，而不是等识别结果出来才发现错字连篇。
 */

export const MAX_EDGE = 2000;
export const JPEG_QUALITY = 0.85;

/** 手写识别可接受的短边下限（px）。 */
export const MIN_SHORT_EDGE = 600;
/** 手写识别可接受的长边下限（px）。 */
export const MIN_LONG_EDGE = 800;

export interface CompressOptions {
  /** 最长边上限（px）。 */
  maxEdge?: number;
  /** JPEG 编码质量（0~1）。 */
  quality?: number;
}

/**
 * 画质是否低于手写识别门槛。
 *
 * 与后端 is_low_resolution 同源：短边 < MIN_SHORT_EDGE 或长边 < MIN_LONG_EDGE。
 * 尺寸未知（无法解码 / 非图片 / 后端未落库）时返回 false——宁可漏提示，也不要把
 * 正常照片判成不合格而挡住老师上传。
 */
export function isLowResolution(
  width: number | null | undefined,
  height: number | null | undefined,
): boolean {
  if (!width || !height) {
    return false;
  }
  const shortEdge = Math.min(width, height);
  const longEdge = Math.max(width, height);
  return shortEdge < MIN_SHORT_EDGE || longEdge < MIN_LONG_EDGE;
}

/**
 * 后端可接收的图片 MIME（与 backend/app/images.py 的 ALLOWED_CONTENT_TYPES 同源）。
 */
export const SUPPORTED_IMAGE_TYPES: readonly string[] = [
  "image/jpeg",
  "image/jpg",
  "image/pjpeg",
  "image/png",
  "image/webp",
  "image/bmp",
  "image/x-ms-bmp",
];

/** 后端可接收的文件后缀（与 ALLOWED_EXTENSIONS 同源）。 */
export const SUPPORTED_IMAGE_EXTENSIONS: readonly string[] = [
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".bmp",
];

/** 后端白名单提示文案，与 ALLOWED_HINT 保持一致。 */
export const ACCEPTED_IMAGE_HINT = "jpg / jpeg / png / webp / bmp";

function fileSuffix(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/**
 * 这张照片是否有可能被后端接收。
 *
 * 判据与后端 resolve_extension 一致：MIME 命中或文件后缀命中，任一即可。
 * 之所以要在选图当场判：iPhone 原片常是 HEIC，浏览器压不动时 processImage 会降级
 * 返回「原文件」，老师点「上传并识别」只会收到一句「仅支持 jpg…」的 400，完全不知道
 * 下一步该做什么（手机端真机验证暴露）。
 */
export function isAcceptedImage(file: File): boolean {
  if (SUPPORTED_IMAGE_TYPES.includes((file.type || "").toLowerCase())) {
    return true;
  }
  return SUPPORTED_IMAGE_EXTENSIONS.includes(fileSuffix(file.name || ""));
}

/** 挑出后端不收的照片名，供页面就地提示。 */
export function listRejectedImages(files: File[]): string[] {
  return files
    .filter((file) => !isAcceptedImage(file))
    .map((file) => file.name || "未命名照片");
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
/** 压缩结果 + 原图尺寸（供画质判定复用同一次解码，避免二次解码开销）。 */
export interface ProcessedImage {
  file: File;
  /** 原图像素宽；无法解析时为 0。 */
  width: number;
  /** 原图像素高；无法解析时为 0。 */
  height: number;
}

/**
 * 压缩单张图片并回报原图尺寸；非图片或压缩失败时返回原文件。
 *
 * @param file 待压缩文件。
 * @param options 压缩参数（默认最长边 2000px、质量 0.85）。
 * @returns 压缩后的文件与原图像素尺寸。
 */
export async function processImage(
  file: File,
  options: CompressOptions = {},
): Promise<ProcessedImage> {
  const maxEdge = options.maxEdge ?? MAX_EDGE;
  const quality = options.quality ?? JPEG_QUALITY;

  if (!file.type.startsWith("image/")) {
    return { file, width: 0, height: 0 }; // 非图片直接短路
  }

  let decoded: Awaited<ReturnType<typeof loadImage>> | null = null;
  try {
    decoded = await loadImage(file);
    const sourceWidth = decoded.width;
    const sourceHeight = decoded.height;
    const { width, height } = targetSize(sourceWidth, sourceHeight, maxEdge);

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) {
      return { file, width: sourceWidth, height: sourceHeight }; // 无 2D 上下文则降级原图
    }
    context.drawImage(decoded.source, 0, 0, width, height);

    const blob = await canvasToBlob(canvas, quality);
    if (!blob) {
      return { file, width: sourceWidth, height: sourceHeight };
    }
    const name = toJpegName(file.name || "photo.jpg");
    return {
      file: new File([blob], name, { type: "image/jpeg", lastModified: file.lastModified }),
      width: sourceWidth,
      height: sourceHeight,
    };
  } catch {
    // 解码/编码失败一律降级原图，不阻断上传；尺寸留 0，画质判定按「不提示」处理
    return { file, width: 0, height: 0 };
  } finally {
    decoded?.revoke();
  }
}

/** 只关心压缩产物的调用方沿用此入口。 */
export async function compressImage(file: File, options: CompressOptions = {}): Promise<File> {
  const processed = await processImage(file, options);
  return processed.file;
}

/** 批量处理（保序），返回压缩结果与原图尺寸。 */
export async function processImages(
  files: File[],
  options: CompressOptions = {},
): Promise<ProcessedImage[]> {
  return Promise.all(files.map((file) => processImage(file, options)));
}

/** 批量压缩（保序）。 */
export async function compressImages(
  files: File[],
  options: CompressOptions = {},
): Promise<File[]> {
  const processed = await processImages(files, options);
  return processed.map((item) => item.file);
}
