import AsrRetranscriptionPanel from "@/components/asr-retranscription-panel";

type PageProps = { params: Promise<{ id: string }> };

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return <AsrRetranscriptionPanel jobId={id} />;
}
