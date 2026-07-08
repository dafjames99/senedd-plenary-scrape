import { NextRequest, NextResponse } from "next/server";
import { getTranscript } from "@/lib/queries";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const meetingId = Number(id);
  if (!Number.isInteger(meetingId)) {
    return NextResponse.json({ error: "invalid meeting id" }, { status: 400 });
  }
  try {
    const transcript = await getTranscript(meetingId);
    if (!transcript) {
      return NextResponse.json({ error: "meeting not found" }, { status: 404 });
    }
    return NextResponse.json(transcript);
  } catch (err) {
    console.error("transcript fetch failed", err);
    return NextResponse.json({ error: "transcript fetch failed" }, { status: 500 });
  }
}
