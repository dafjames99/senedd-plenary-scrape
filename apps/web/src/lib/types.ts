/** Shared shapes between the data layer, API routes, and UI components. */

export interface MeetingSummary {
  meetingId: number;
  meetingDate: string; // ISO
  meetingType: string;
  speechCount: number;
  /** Recorded divisions during the meeting (0 = none). Drives the vote tag. */
  voteCount: number;
}

/** One agenda item in the LHS table of contents (built by lib/agenda.ts). */
export interface AgendaTocEntry {
  agendaItemId: string;
  /** Official number parsed from the title ("1. Questions…"); null if unnumbered. */
  number: string | null;
  /** Title with any leading number stripped — the ToC renders them separately. */
  title: string;
  /** First speech of the item; ToC clicks jump the video/transcript here. */
  firstSpeechId: number;
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
  /** SeneddTV player base URL (from the webcast GUID); startPos layered on per
   *  jump. Null when the meeting has no resolved GUID → pane hides. */
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
