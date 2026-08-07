import LiveJobPage from "@/components/live-job-page";
import JobControls from "@/components/job-controls";
import Link from "next/link";

type PageProps = { params: Promise<{ id: string }> };

const linkStyle = {
  minHeight: "48px",
  padding: "0 18px",
  borderRadius: "12px",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  color: "#27324a",
  background: "#fff",
  border: "2px solid #aeb9c8",
  boxShadow: "0 10px 28px rgba(15,23,42,.16)",
  fontSize: "1rem",
  fontWeight: 750,
} as const;

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return (
    <div style={{ paddingBottom: "140px" }}>
      <LiveJobPage jobId={id} />
      <nav
        aria-label="任務延伸功能"
        style={{
          position: "fixed",
          right: "24px",
          bottom: "104px",
          zIndex: 70,
          display: "flex",
          gap: "10px",
          flexWrap: "wrap",
          justifyContent: "flex-end",
        }}
      >
        <Link href={`/jobs/${id}/performance`} style={linkStyle}>效能與費用分析</Link>
        <Link href={`/subtitles/${id}`} style={{ ...linkStyle, color: "#fff", background: "#047857", borderColor: "#047857" }}>字幕校訂中心</Link>
        <Link href={`/jobs/${id}/review`} style={{ ...linkStyle, color: "#fff", background: "#3730a3", borderColor: "#3730a3" }}>完整審查與輸出</Link>
      </nav>
      <JobControls jobId={id} />
    </div>
  );
}
