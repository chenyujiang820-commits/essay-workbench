import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Stars from "./Stars";

describe("Stars", () => {
  it("renders nothing for an unscored essay (no empty star row)", () => {
    const { container } = render(<Stars stars={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("fills and blanks add up to max", () => {
    const { getByTestId } = render(<Stars stars={3} />);
    const node = getByTestId("stars");
    expect(node.textContent).toContain("★★★");
    expect(node.textContent).toContain("☆☆");
    expect(node.getAttribute("aria-label")).toBe("3 星");
  });

  it("shows the score only when the caller passes it", () => {
    const plain = render(<Stars stars={5} />);
    expect(plain.queryByTestId("stars-score")).toBeNull();
    // 查询绑定在 document.body 上：不先卸载，第二次 render 会同时看见两颗星。
    plain.unmount();

    const withScore = render(<Stars stars={5} score={92} />);
    expect(withScore.getByTestId("stars-score").textContent).toBe("92 分");
    expect(withScore.getByTestId("stars").getAttribute("aria-label")).toBe("5 星（92 分）");
  });
});
