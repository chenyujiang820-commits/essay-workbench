import PlaceholderPage from "../components/PlaceholderPage";

/** 校对环核心：左原片右文字、存疑高亮、低置信横幅（T03 实现）。 */
export default function ProofreadPage() {
  return (
    <PlaceholderPage
      title="逐句校对"
      description="左侧原片可缩放，右侧文本可编辑；replace/delete/insert 段落黄底标「存疑」，整篇低置信显示横幅。"
    />
  );
}
