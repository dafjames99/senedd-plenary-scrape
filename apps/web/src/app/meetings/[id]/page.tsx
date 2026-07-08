import { notFound } from "next/navigation";
import { getTranscript, searchMeetings } from "@/lib/queries";
import Workspace from "@/components/Workspace";

export const dynamic = "force-dynamic";

export default async function MeetingPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ speech?: string }>;
}) {
  const { id } = await params;
  const { speech } = await searchParams;
  const meetingId = Number(id);
  if (!Number.isInteger(meetingId)) notFound();

  const [transcript, meetings] = await Promise.all([
    getTranscript(meetingId),
    searchMeetings({}),
  ]);
  if (!transcript) notFound();

  const initialSpeechId = speech ? Number(speech) : null;

  return (
    <Workspace
      transcript={transcript}
      initialMeetings={meetings}
      initialSpeechId={Number.isInteger(initialSpeechId) ? initialSpeechId : null}
      videoMode={(process.env.NEXT_PUBLIC_VIDEO_MODE ?? "embed") as "embed" | "link" | "off"}
    />
  );
}
