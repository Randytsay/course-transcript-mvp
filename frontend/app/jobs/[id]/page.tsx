import LiveJobPage from "@/components/live-job-page";

type PageProps = {
  params: Promise<{ id: string }>;
};

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return <LiveJobPage jobId={id} />;
}
