import { getCitationData, keywordSpeechSearch } from "./queries";
import type { AskBlock, AskResponse } from "./types";

/**
 * Deterministic ask-mode fallback for environments without an Anthropic API
 * key. Retrieves real speeches by keyword overlap and renders them through
 * the same citation-block grammar the live loop produces, so the UI (and the
 * marker↔card contract) is fully exercisable offline. Clearly labelled.
 */
export async function mockAsk(question: string): Promise<AskResponse> {
  const ids = await keywordSpeechSearch(question, 3);
  const citations = await getCitationData(ids);

  if (citations.length === 0) {
    return {
      mode: "mock",
      blocks: [
        {
          type: "notice",
          text: "Mock mode: no speeches matched your question by keyword. Set ANTHROPIC_API_KEY and SENEDD_MCP_URL for real semantic answers.",
        },
      ],
    };
  }

  const blocks: AskBlock[] = [
    {
      type: "notice",
      text: "Mock mode — keyword retrieval over real speeches, no LLM. Set ANTHROPIC_API_KEY + SENEDD_MCP_URL for live answers.",
    },
    {
      type: "prose",
      text:
        `The record contains ${citations.length} speech${citations.length > 1 ? "es" : ""} relevant to “${question.trim()}”. ` +
        citations
          .map((c, i) => `${c.speaker} spoke on ${c.agendaTitle ?? "the floor"} [${i + 1}]`)
          .join("; ") +
        ".",
    },
    ...citations.map((c, i): AskBlock => ({
      type: "citation",
      marker: i + 1,
      speechId: c.speechId,
      speaker: c.speaker,
      meetingId: c.meetingId,
      meetingDate: c.meetingDate,
      agendaTitle: c.agendaTitle,
      quote: c.text.length > 280 ? c.text.slice(0, 277) + "…" : c.text,
      startPos: c.startPos,
      tvUrl: c.tvUrl,
    })),
  ];

  return { mode: "mock", blocks };
}
