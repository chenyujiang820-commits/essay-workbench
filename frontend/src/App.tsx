import { Navigate, Route, Routes } from "react-router-dom";

import BookPage from "./pages/BookPage";
import EssayListPage from "./pages/EssayListPage";
import IssuePage from "./pages/IssuePage";
import LoginPage from "./pages/LoginPage";
import PresentPage from "./pages/PresentPage";
import ProofreadPage from "./pages/ProofreadPage";
import UploadPage from "./pages/UploadPage";

/** 7 个页面路由表（一期）。 */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<IssuePage />} />
      <Route path="/issues/:issueId/upload" element={<UploadPage />} />
      <Route path="/issues/:issueId/essays" element={<EssayListPage />} />
      <Route path="/essays/:essayId/proofread" element={<ProofreadPage />} />
      <Route path="/issues/:issueId/book" element={<BookPage />} />
      <Route path="/issues/:issueId/present" element={<PresentPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
