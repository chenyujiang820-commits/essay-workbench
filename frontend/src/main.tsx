import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles.css";

const container = document.getElementById("root");

if (container) {
  // 数据路由：启用 useBlocker 等数据路由能力（校对页离开确认依赖它）。
  const router = createBrowserRouter([{ path: "*", element: <App /> }]);

  ReactDOM.createRoot(container).render(
    <React.StrictMode>
      <RouterProvider router={router} />
    </React.StrictMode>,
  );
}
