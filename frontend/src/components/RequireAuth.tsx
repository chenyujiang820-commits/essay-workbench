/** 路由守卫：无登录态时重定向到登录页。 */

import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAppStore } from "../store";

export default function RequireAuth() {
  const token = useAppStore((state) => state.token);
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
