-- Basic Data Viewing Queries
-- Run these to get familiar with the data structure

-- 1. View all speeches with speaker name and agenda
SELECT
    s.speech_id,
    m.name_english as speaker,
    rc.agenda_item_english as agenda,
    LENGTH(s.speech_text) as text_length,
    s.speech_language
FROM
    speeches s
    LEFT JOIN members m ON s.speaker_id = m.member_id
    LEFT JOIN (
        SELECT
            agenda_item_id,
            meeting_id,
            agenda_item_english
        FROM raw_contributions
    ) AS rc ON s.agenda_item_id = rc.agenda_item_id
    AND s.meeting_id = rc.meeting_id
ORDER BY s.speech_id
LIMIT 20;
-- GARBAGE QUERY

-- 2. View members and their role
SELECT
    member_id,
    name_english,
    job_title_english,
    sort_code
FROM members
ORDER BY sort_code
LIMIT 15;

-- 3. Count meetings and contributions
SELECT
    m.meeting_id,
    m.meeting_date,
    COUNT(DISTINCT rc.contribution_id) as total_contributions,
    COUNT(DISTINCT s.speech_id) as total_speeches
FROM
    meetings m
    LEFT JOIN raw_contributions rc ON m.meeting_id = rc.meeting_id
    LEFT JOIN speeches s ON m.meeting_id = s.meeting_id
GROUP BY
    m.meeting_id
ORDER BY m.meeting_date DESC;

-- 4. View procedural events
SELECT
    event_type,
    raw_text,
    contribution_type
FROM procedural_events
LIMIT 10;
-- GARBAGE QUERY

-- 5. Latest sync checkpoint
SELECT
    checkpoint_id,
    last_sync_date,
    last_meeting_id,
    file_count,
    status,
    notes
FROM sync_checkpoints
ORDER BY created_at DESC
LIMIT 1;