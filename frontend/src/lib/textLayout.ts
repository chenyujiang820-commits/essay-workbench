export const DISPLAY_LINE_WIDTH = 25;

export function wrapDisplayLine(text: string, width = DISPLAY_LINE_WIDTH): string {
  if (width < 1 || text.length <= width) {
    return text;
  }
  const lines: string[] = [];
  for (let start = 0; start < text.length; start += width) {
    lines.push(text.slice(start, start + width));
  }
  return lines.join("\n");
}

export function formatParagraphsForDisplay(
  paragraphs: string[],
  width = DISPLAY_LINE_WIDTH,
): string[] {
  return paragraphs.map((paragraph) => wrapDisplayLine(paragraph, width));
}
