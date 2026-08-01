import LiveJobPage from "@/components/live-job-page";
import Link from "next/link";

type PageProps = {
  params: Promise<{ id: string }>;
};

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return (
    <>
      <LiveJobPage jobId={id} />
      <Link
        href={`/jobs/${id}/review`}
        style={{
          position: "fixed",
          right: "24px",
          bottom: "24px",
          zIndex: 70,
          minHeight: "48px",
          padding: "0 18px",
          borderRadius: "12px",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#fff",
          background: "#3730a3",
          boxShadow: "0 10px 28px rgba(55,48,163,.28)",
          fontSize: "1rem",
          fontWeight: 750,
        }}
      >
        完整審查與輸出
      </Link>
    </>
  );
}
