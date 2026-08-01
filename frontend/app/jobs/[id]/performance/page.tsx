import PerformancePage from "@/components/performance-page";

type PageProps = {
  params: Promise<{ id: string }>;
};

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return <PerformancePage jobId={id} />;
}
