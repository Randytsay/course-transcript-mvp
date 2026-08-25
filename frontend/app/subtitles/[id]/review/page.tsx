import AIReviewPanel from "@/components/ai-subtitle-review";

type PageProps = { params: Promise<{ id: string }> };

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return <AIReviewPanel subtitleId={id} />;
}
