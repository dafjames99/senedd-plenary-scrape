import { getPool } from "./db";
import { parseStartPos, playerBaseUrl } from "./tv";
import type { MeetingSummary, Transcript, TranscriptSpeech } from "./types";

/**
 * Read-only SQL over the pipeline's schema. Shared by the RSC pages and the
 * API route handlers so both render paths query identically (PRD §4).
 */

export async function searchMeetings(opts: {
  q?: string | null;
  from?: string | null;
  to?: string | null;
  limit?: number;
}): Promise<MeetingSummary[]> {
  const { q = null, from = null, to = null, limit = 25 } = opts;
  const { rows } = await getPool().query(
    `
    SELECT m.meeting_id,
           m.meeting_date,
           m.meeting_type,
           COUNT(DISTINCT s.speech_id)::int AS speech_count,
           ARRAY(
             SELECT DISTINCT rc.agenda_item_english
             FROM raw_contributions rc
             WHERE rc.meeting_id = m.meeting_id
               AND rc.agenda_item_english IS NOT NULL
           ) AS agenda_items
    FROM meetings m
    LEFT JOIN speeches s ON s.meeting_id = m.meeting_id
    WHERE m.meeting_type = 'plenary'
      AND ($1::text IS NULL OR EXISTS (
            SELECT 1 FROM raw_contributions rc
            WHERE rc.meeting_id = m.meeting_id
              AND (rc.agenda_item_english ILIKE '%' || $1 || '%'
                   OR rc.member_name_english ILIKE '%' || $1 || '%')
          ))
      AND ($2::date IS NULL OR m.meeting_date >= $2)
      AND ($3::date IS NULL OR m.meeting_date <= $3)
    GROUP BY m.meeting_id
    HAVING COUNT(DISTINCT s.speech_id) > 0
    ORDER BY m.meeting_date DESC
    LIMIT $4
    `,
    [q, from, to, limit],
  );
  return rows.map((r) => ({
    meetingId: r.meeting_id,
    meetingDate: r.meeting_date.toISOString(),
    meetingType: r.meeting_type,
    speechCount: r.speech_count,
    agendaItems: r.agenda_items,
  }));
}

export async function latestMeetingId(): Promise<number | null> {
  const { rows } = await getPool().query(
    `SELECT m.meeting_id
     FROM meetings m
     WHERE m.meeting_type = 'plenary'
       AND EXISTS (SELECT 1 FROM speeches s WHERE s.meeting_id = m.meeting_id)
     ORDER BY m.meeting_date DESC LIMIT 1`,
  );
  return rows[0]?.meeting_id ?? null;
}

export async function getTranscript(meetingId: number): Promise<Transcript | null> {
  const pool = getPool();

  const meetingRes = await pool.query(
    `SELECT m.meeting_id, m.meeting_date, m.meeting_type, m.webcast_guid,
            COUNT(DISTINCT s.speech_id)::int AS speech_count
     FROM meetings m
     LEFT JOIN speeches s ON s.meeting_id = m.meeting_id
     WHERE m.meeting_id = $1
     GROUP BY m.meeting_id`,
    [meetingId],
  );
  if (meetingRes.rows.length === 0) return null;
  const m = meetingRes.rows[0];

  // Agenda titles come from raw_contributions (speeches carry only the id).
  const agendaRes = await pool.query(
    `SELECT DISTINCT ON (rc.agenda_item_id)
            rc.agenda_item_id, rc.agenda_item_english
     FROM raw_contributions rc
     WHERE rc.meeting_id = $1 AND rc.agenda_item_id IS NOT NULL
     ORDER BY rc.agenda_item_id`,
    [meetingId],
  );
  const agendaTitles = new Map<string, string>(
    agendaRes.rows.map((r) => [r.agenda_item_id, r.agenda_item_english ?? r.agenda_item_id]),
  );

  // One row per speech: text + earliest part's order/URL (= the speech's start).
  const speechRes = await pool.query(
    `SELECT s.speech_id, s.speaker_name, s.agenda_item_id, s.speech_text,
            f.ord,
            f.spoken_url
     FROM speeches s
     JOIN LATERAL (
       SELECT MIN(sp.contribution_order_id) AS ord,
              (SELECT sp2.spoken_url FROM speech_parts sp2
               WHERE sp2.speech_id = s.speech_id AND sp2.spoken_url IS NOT NULL
               ORDER BY sp2.contribution_order_id ASC LIMIT 1) AS spoken_url
       FROM speech_parts sp
       WHERE sp.speech_id = s.speech_id
     ) f ON true
     WHERE s.meeting_id = $1
     ORDER BY f.ord ASC`,
    [meetingId],
  );

  const speeches: TranscriptSpeech[] = speechRes.rows.map((r) => ({
    speechId: r.speech_id,
    speakerName: r.speaker_name,
    agendaItemId: r.agenda_item_id,
    agendaTitle: agendaTitles.get(r.agenda_item_id) ?? r.agenda_item_id,
    text: r.speech_text,
    startPos: parseStartPos(r.spoken_url),
    tvUrl: r.spoken_url,
  }));

  return {
    meeting: {
      meetingId: m.meeting_id,
      meetingDate: m.meeting_date.toISOString(),
      meetingType: m.meeting_type,
      speechCount: m.speech_count,
      agendaItems: [...agendaTitles.values()],
    },
    // The embeddable player, keyed by the meeting's webcast GUID (resolved at
    // ingest). Null when unresolved → the pane hides and watch-links stand in.
    videoBaseUrl: playerBaseUrl(m.webcast_guid ?? null),
    speeches,
  };
}

/** Citation metadata for a set of speech ids (used by /api/ask). */
export async function getCitationData(speechIds: number[]) {
  if (speechIds.length === 0) return [];
  const { rows } = await getPool().query(
    `SELECT s.speech_id, s.speaker_name, s.meeting_id, s.speech_text,
            m.meeting_date,
            rc.agenda_item_english,
            (SELECT sp.spoken_url FROM speech_parts sp
             WHERE sp.speech_id = s.speech_id AND sp.spoken_url IS NOT NULL
             ORDER BY sp.contribution_order_id ASC LIMIT 1) AS spoken_url
     FROM speeches s
     JOIN meetings m ON m.meeting_id = s.meeting_id
     LEFT JOIN LATERAL (
       SELECT rc.agenda_item_english FROM raw_contributions rc
       WHERE rc.meeting_id = s.meeting_id AND rc.agenda_item_id = s.agenda_item_id
         AND rc.agenda_item_english IS NOT NULL
       LIMIT 1
     ) rc ON true
     WHERE s.speech_id = ANY($1::int[])`,
    [speechIds],
  );
  return rows.map((r) => ({
    speechId: r.speech_id as number,
    speaker: r.speaker_name as string,
    meetingId: r.meeting_id as number,
    meetingDate: (r.meeting_date as Date).toISOString(),
    agendaTitle: (r.agenda_item_english as string | null) ?? null,
    text: r.speech_text as string,
    tvUrl: (r.spoken_url as string | null) ?? null,
    startPos: parseStartPos(r.spoken_url),
  }));
}

/** Mock-mode retrieval: rank speeches by naive keyword overlap with the query. */
export async function keywordSpeechSearch(query: string, limit = 3) {
  const words = [...new Set(query.toLowerCase().split(/\W+/).filter((w) => w.length > 3))];
  if (words.length === 0) return [];
  const { rows } = await getPool().query(
    `SELECT s.speech_id,
            (SELECT COUNT(*) FROM unnest($1::text[]) w
             WHERE s.speech_text ILIKE '%' || w || '%')::int AS hits
     FROM speeches s
     ORDER BY hits DESC, s.speech_id ASC
     LIMIT $2`,
    [words, limit],
  );
  return rows.filter((r) => r.hits > 0).map((r) => r.speech_id as number);
}
