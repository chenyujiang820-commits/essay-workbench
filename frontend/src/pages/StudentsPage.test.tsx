import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import type { Student } from "../api/types";
import StudentsPage, { parseRosterText } from "./StudentsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStudents: vi.fn(),
      createStudent: vi.fn(),
      updateStudent: vi.fn(),
      deactivateStudent: vi.fn(),
      importStudents: vi.fn(),
    },
  };
});

const students: Student[] = [
  { id: 1, student_no: "20230101", name: "张三", active: 1 },
  { id: 2, student_no: "20230102", name: "李四", active: 1 },
];

function renderStudents() {
  const router = createMemoryRouter(
    [
      { path: "/students", element: <StudentsPage /> },
      { path: "/", element: <div>期数列表页</div> },
    ],
    { initialEntries: ["/students"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("parseRosterText", () => {
  it("parses comma / Chinese comma / whitespace lines and skips bad lines", () => {
    const rows = parseRosterText(
      "20230101,张三\n20230102，李四\n20230103 王五\n\n错误行\n20230101,重复跳过",
    );
    expect(rows).toEqual([
      { student_no: "20230101", name: "张三" },
      { student_no: "20230102", name: "李四" },
      { student_no: "20230103", name: "王五" },
    ]);
  });
});

describe("StudentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listStudents).mockResolvedValue(students);
  });

  it("lists students and goes back home", async () => {
    renderStudents();
    await screen.findByText("张三");

    expect(screen.getByText("李四")).toBeTruthy();
    fireEvent.click(screen.getByTestId("back-home"));
    await waitFor(() => expect(screen.getByText("期数列表页")).toBeTruthy());
  });

  it("adds a student and reloads the list", async () => {
    vi.mocked(api.createStudent).mockResolvedValue({
      id: 3,
      student_no: "20230103",
      name: "王五",
      active: 1,
    });
    renderStudents();
    await screen.findByText("张三");

    fireEvent.change(screen.getByPlaceholderText("如 20230101"), {
      target: { value: "20230103" },
    });
    fireEvent.change(screen.getByPlaceholderText("如 张三"), {
      target: { value: "王五" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加学生" }));

    await waitFor(() =>
      expect(api.createStudent).toHaveBeenCalledWith("20230103", "王五"),
    );
    expect(await screen.findByText("已添加 王五")).toBeTruthy();
  });

  it("imports pasted roster and reports created/updated counts", async () => {
    vi.mocked(api.importStudents).mockResolvedValue({ created: 2, updated: 1 });
    renderStudents();
    await screen.findByText("张三");

    fireEvent.click(screen.getByRole("button", { name: "批量导入" }));
    fireEvent.change(screen.getByTestId("import-text"), {
      target: { value: "20230101,张三新\n20230103,王五\n20230104,赵六" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));

    await waitFor(() =>
      expect(api.importStudents).toHaveBeenCalledWith([
        { student_no: "20230101", name: "张三新" },
        { student_no: "20230103", name: "王五" },
        { student_no: "20230104", name: "赵六" },
      ]),
    );
    expect(await screen.findByText(/新增 2 人，更新 1 人/)).toBeTruthy();
  });

  it("deactivates a student after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.deactivateStudent).mockResolvedValue({ id: 2 });
    renderStudents();
    await screen.findByText("李四");

    fireEvent.click(screen.getAllByRole("button", { name: "停用" })[1]);
    await waitFor(() => expect(api.deactivateStudent).toHaveBeenCalledWith(2));
    expect(await screen.findByText("李四 已停用。")).toBeTruthy();
    confirmSpy.mockRestore();
  });
});
