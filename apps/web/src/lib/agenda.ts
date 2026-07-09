import type { AgendaTocEntry, TranscriptSpeech } from "./types";

/** Official item numbering embedded in agenda titles: "1. Questions…" / "2) …". */
const LEADING_NUMBER = /^\s*(\d+)[.)]\s*/;

/**
 * Split an agenda title into its official leading number (if any) and the
 * remaining text. Unnumbered items (e.g. pre-meeting business whose English
 * title is missing) return `number: null` — callers render a neutral mark
 * rather than inventing an ordinal that could contradict the official order.
 */
export function splitAgendaTitle(title: string): { number: string | null; rest: string } {
  const m = title.match(LEADING_NUMBER);
  return m ? { number: m[1], rest: title.slice(m[0].length) } : { number: null, rest: title };
}

/** Unique agenda items in first-appearance order, each anchored to its first speech. */
export function agendaToc(speeches: TranscriptSpeech[]): AgendaTocEntry[] {
  const out: AgendaTocEntry[] = [];
  const seen = new Set<string>();
  for (const s of speeches) {
    if (seen.has(s.agendaItemId)) continue;
    seen.add(s.agendaItemId);
    const { number, rest } = splitAgendaTitle(s.agendaTitle);
    out.push({
      agendaItemId: s.agendaItemId,
      number,
      title: rest,
      firstSpeechId: s.speechId,
    });
  }
  return out;
}
