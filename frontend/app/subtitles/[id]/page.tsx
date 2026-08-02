import SubtitleEditor from "@/components/subtitle-editor";

type PageProps = { params: Promise<{ id: string }> };

export default async function Page({ params }: PageProps) {
  const { id } = await params;
  return <SubtitleEditor subtitleId={id} />;
}
