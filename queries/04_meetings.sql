-- Meetings & Agendas Queries
-- Analyze meeting structure, agenda items, and session organization

-- 1. Meeting summary with contribution and speech counts
SELECT 
  m.meeting_id,
  m.assembly,
  m.meeting_date,
  COUNT(DISTINCT rc.contribution_id) as total_contributions,
  COUNT(DISTINCT s.speech_id) as total_speeches,
  COUNT(DISTINCT rc.member_id) as unique_speakers,
  COUNT(DISTINCT rc.agenda_item_id) as agenda_items
FROM meetings m
LEFT JOIN raw_contributions rc ON m.meeting_id = rc.meeting_id
LEFT JOIN speeches s ON m.meeting_id = s.meeting_id
GROUP BY m.meeting_id
ORDER BY m.meeting_date DESC;

-- 2. Agenda items and their contribution breakdown
SELECT 
  rc.agenda_item_id,
  rc.agenda_item_english,
  COUNT(rc.contribution_id) as contributions,
  COUNT(DISTINCT s.speech_id) as speeches,
  COUNT(DISTINCT rc.member_id) as speakers,
  SUM(LENGTH(s.speech_text)) as total_chars
FROM raw_contributions rc
LEFT JOIN speech_parts sp ON rc.contribution_id = sp.contribution_id
LEFT JOIN speeches s ON sp.speech_id = s.speech_id
WHERE rc.meeting_id = 16060  -- Example meeting
GROUP BY rc.agenda_item_id
ORDER BY rc.contribution_order_id;

-- 3. Procedural vs substantive contributions per meeting
SELECT 
  m.meeting_date,
  SUM(CASE WHEN cc.row_type = 'SPEECH' THEN 1 ELSE 0 END) as speeches,
  SUM(CASE WHEN cc.row_type = 'PROCEDURAL' THEN 1 ELSE 0 END) as procedural,
  SUM(CASE WHEN cc.row_type = 'NOISE' THEN 1 ELSE 0 END) as noise,
  COUNT(*) as total
FROM meetings m
LEFT JOIN raw_contributions rc ON m.meeting_id = rc.meeting_id
LEFT JOIN classified_contributions cc ON rc.contribution_id = cc.contribution_id
GROUP BY m.meeting_id
ORDER BY m.meeting_date DESC;

-- 4. Contribution types breakdown
SELECT 
  contribution_type,
  COUNT(*) as count,
  GROUP_CONCAT(DISTINCT contribution_language, ', ') as languages
FROM raw_contributions
WHERE meeting_id = 16060  -- Example meeting
GROUP BY contribution_type
ORDER BY count DESC;

-- 5. Time-based analysis of contributions
SELECT 
  TIME(contribution_time) as time_slot,
  COUNT(*) as contributions,
  COUNT(DISTINCT member_id) as unique_speakers
FROM raw_contributions
WHERE contribution_time IS NOT NULL
GROUP BY TIME(contribution_time)
ORDER BY time_slot;

-- 6. All meetings and assembly sessions
SELECT 
  DISTINCT assembly,
  COUNT(DISTINCT meeting_id) as num_meetings,
  MIN(meeting_date) as first_meeting,
  MAX(meeting_date) as last_meeting
FROM meetings
GROUP BY assembly
ORDER BY assembly DESC;
