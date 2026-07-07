/** Shared shapes between the data layer, API routes, and UI components. */

export interface MeetingSummary {
  meetingId: number;
  meetingDate: string; // ISO
  meetingType: string;
  speechCount: number;
  agendaItems: string[];
}

export interface AgendaItem {
  agendaItemId: string;
  title: string;
}

export interface TranscriptSpeech {
  speechId: number;
  speakerName: string;
  agendaItemId: string;
  agendaTitle: string;
  text: string;
  /** Seconds offset into the meeting's SeneddTV clip (from the stored URL). */
  startPos: number | null;
  /** The speech's own SeneddTV URL (jump-to-moment). */
  tvUrl: string | null;
}

export interface Transcript {
  meeting: MeetingSummary;
  /** Clip URL without startPos — the video pane's base src. */
  videoBaseUrl: string | null;
  speeches: TranscriptSpeech[];
}

/** Typed output blocks for the ask box (PRD §5: markers + citation cards). */
export type AskBlock =
  | { type: "prose"; text: string }
  | {
      type: "citation";
      marker: number;
      speechId: number;
      speaker: string;
      meetingId: number;
      meetingDate: string;
      agendaTitle: string | null;
      quote: string;
      startPos: number | null;
      tvUrl: string | null;
    }
  | { type: "notice"; text: string };

export interface AskResponse {
  blocks: AskBlock[];
  mode: "live" | "mock";
}
