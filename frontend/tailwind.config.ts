import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // 中文优先字体栈：Windows 开发 / Linux 部署均可读
        sans: ["PingFang SC", "Microsoft YaHei", "Noto Sans SC", "sans-serif"],
      },
      fontSize: {
        present: ["2.5rem", { lineHeight: "1.6" }],
        "present-lg": ["3.5rem", { lineHeight: "1.5" }],
      },
    },
  },
  plugins: [],
} satisfies Config;
