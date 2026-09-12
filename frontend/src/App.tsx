import { Navigate, Route, Routes } from "react-router-dom";

import RequireAuth from "./components/RequireAuth";
import BookPage from "./pages/BookPage";
import PortfolioPage from "./pages/PortfolioPage";
import RankingPage from "./pages/RankingPage";
import SharePage from "./pages/SharePage";
import SharesPage from "./pages/SharesPage";
import EssayListPage from "./pages/EssayListPage";
import IssuePage from "./pages/IssuePage";
import LoginPage from "./pages/LoginPage";
import PresentPage from "./pages/PresentPage";
import ProofreadPage from "./pages/ProofreadPage";
import StudentsPage from "./pages/StudentsPage";
import UploadPage from "./pages/UploadPage";

/**
 * 路由表：家长只读页与家长分享的预览免鉴权，其余（含二期看板/档案/分享管理）都要登录。
 *
 * 免鉴权面只有一个前缀：``/share/:token``（页面）+ ``/api/share/*``（数据），
 * 二者都只出已定稿内容且不含学号与分数（PRD v1.3 §9 回归锁）。
 */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* 家长页免鉴权：家长没有账号，也拿不到任何管理入口；令牌无效由页面自己渲染失效态。 */}
      <Route path="/share/:token?" element={<SharePage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<IssuePage />} />
        <Route path="/students" element={<StudentsPage />} />
        <Route path="/issues/:issueId/upload" element={<UploadPage />} />
        <Route path="/issues/:issueId/essays" element={<EssayListPage />} />
        <Route path="/essays/:essayId/proofread" element={<ProofreadPage />} />
        <Route path="/issues/:issueId/book" element={<BookPage />} />
        <Route path="/present/:issueId" element={<PresentPage />} />
        <Route path="/issues/:issueId/ranking" element={<RankingPage />} />
        <Route path="/issues/:issueId/shares" element={<SharesPage />} />
        <Route path="/students/:studentId/portfolio" element={<PortfolioPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
