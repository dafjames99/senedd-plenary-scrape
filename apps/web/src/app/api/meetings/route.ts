import { NextRequest, NextResponse } from "next/server";
import { searchMeetings } from "@/lib/queries";

export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;
  try {
    const meetings = await searchMeetings({
      q: params.get("q"),
      from: params.get("from"),
      to: params.get("to"),
    });
    return NextResponse.json({ meetings });
  } catch (err) {
    console.error("meeting search failed", err);
    return NextResponse.json({ error: "meeting search failed" }, { status: 500 });
  }
}
