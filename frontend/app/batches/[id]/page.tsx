import BatchDetailPage from "@/components/batch-detail-page";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BatchDetailPage batchId={id} />;
}
