import { NextRequest, NextResponse } from "next/server";
import { mockAsk } from "@/lib/mock-ask";
import type { AskResponse } from "@/lib/types";

export const maxDuration = 60; // Vercel: the tool loop can take a while

export async function POST(req: NextRequest) {
  let question: string;
  try {
    const body = await req.json();
    question = String(body?.question ?? "").trim();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!question) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const liveConfigured = Boolean(process.env.ANTHROPIC_API_KEY && process.env.SENEDD_MCP_URL);

  try {
    let result: AskResponse;
    if (liveConfigured) {
      // Import lazily so mock-only deployments never load the SDKs.
      const { liveAsk } = await import("@/lib/ask");
      result = await liveAsk(question);
    } else {
      result = await mockAsk(question);
    }
    return NextResponse.json(result);
  } catch (err) {
    console.error("ask failed", err);
    const message = err instanceof Error ? err.message : "ask failed";
    return NextResponse.json(
      { blocks: [{ type: "notice", text: `Something went wrong: ${message}` }], mode: liveConfigured ? "live" : "mock" },
      { status: 500 },
    );
  }
}
