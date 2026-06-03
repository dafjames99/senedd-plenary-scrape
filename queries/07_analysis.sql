-- Advanced Analysis Queries
-- Deeper insights into speech patterns, language, and parliamentary behavior

-- 1. Speech length distribution
SELECT 
  CASE
    WHEN LENGTH(speech_text) < 500 THEN 'Short (< 500 chars)'
    WHEN LENGTH(speech_text) < 2000 THEN 'Medium (500-2000)'
    WHEN LENGTH(speech_text) < 5000 THEN 'Long (2000-5000)'
    ELSE 'Very Long (> 5000)'
  END as length_category,
  COUNT(*) as speech_count,
  ROUND(AVG(LENGTH(speech_text))) as avg_length,
  MIN(LENGTH(speech_text)) as min,
  MAX(LENGTH(speech_text)) as max
FROM speeches
GROUP BY length_category
ORDER BY speech_count DESC;

-- 2. Top speakers by total words (assuming avg 5 chars per word)
SELECT 
  m.name_english,
  m.job_title_english,
  COUNT(s.speech_id) as num_speeches,
  SUM(LENGTH(s.speech_text)) as total_chars,
  ROUND(SUM(LENGTH(s.speech_text)) / 5) as estimated_words,
  ROUND(AVG(LENGTH(s.speech_text))) as avg_speech_length
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
GROUP BY m.member_id
HAVING COUNT(s.speech_id) > 0
ORDER BY total_chars DESC
LIMIT 15;

-- 3. Welsh vs English language breakdown
SELECT 
  language_detected,
  COUNT(*) as speeches,
  SUM(LENGTH(speech_text)) as total_chars,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM speeches), 1) as percent_of_speeches,
  ROUND(100.0 * SUM(LENGTH(speech_text)) / (SELECT SUM(LENGTH(speech_text)) FROM speeches), 1) as percent_of_chars
FROM speeches
WHERE language_detected IN ('CY', 'EN')
GROUP BY language_detected
ORDER BY speeches DESC;

-- 4. Bilingual members (speak in both Welsh and English)
SELECT 
  m.name_english,
  m.job_title_english,
  SUM(CASE WHEN s.language_detected = 'CY' THEN 1 ELSE 0 END) as welsh_speeches,
  SUM(CASE WHEN s.language_detected = 'EN' THEN 1 ELSE 0 END) as english_speeches,
  COUNT(DISTINCT s.speech_id) as total_speeches
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
GROUP BY m.member_id
HAVING SUM(CASE WHEN s.language_detected = 'CY' THEN 1 ELSE 0 END) > 0
  AND SUM(CASE WHEN s.language_detected = 'EN' THEN 1 ELSE 0 END) > 0
ORDER BY total_speeches DESC;

-- 5. Agenda item engagement (which topics get most coverage?)
SELECT 
  SUBSTR(rc.agenda_item_english, 1, 60) as agenda_item,
  COUNT(DISTINCT rc.contribution_id) as contributions,
  COUNT(DISTINCT s.speech_id) as speeches,
  COUNT(DISTINCT rc.member_id) as participants,
  SUM(LENGTH(s.speech_text)) as total_chars
FROM raw_contributions rc
LEFT JOIN speech_parts sp ON rc.contribution_id = sp.contribution_id
LEFT JOIN speeches s ON sp.speech_id = s.speech_id
GROUP BY rc.agenda_item_id
ORDER BY total_chars DESC
LIMIT 15;

-- 6. Question vs Statement (contribution types)
SELECT 
  rc.contribution_type,
  CASE
    WHEN rc.contribution_type = 'C' THEN 'Contribution'
    WHEN rc.contribution_type = 'O' THEN 'Oral Question'
    WHEN rc.contribution_type = 'B' THEN 'Business'
    WHEN rc.contribution_type = 'I' THEN 'Information'
    ELSE 'Other'
  END as type_name,
  COUNT(*) as count,
  COUNT(DISTINCT rc.member_id) as speakers
FROM raw_contributions rc
GROUP BY rc.contribution_type
ORDER BY count DESC;

-- 7. Video synchronization URLs (for media integration)
SELECT 
  rc.contribution_id,
  m.name_english as speaker,
  rc.contribution_time as timestamp,
  rc.contribution_spoken_seneddtv as video_url_cy,
  rc.contribution_translated_seneddtv as video_url_en
FROM raw_contributions rc
LEFT JOIN members m ON rc.member_id = m.member_id
WHERE rc.contribution_spoken_seneddtv IS NOT NULL
   OR rc.contribution_translated_seneddtv IS NOT NULL
LIMIT 20;

-- 8. Most active agenda items (by number of speakers)
SELECT 
  SUBSTR(rc.agenda_item_english, 1, 50) as agenda_item,
  COUNT(DISTINCT rc.member_id) as unique_speakers,
  COUNT(DISTINCT rc.contribution_id) as contributions,
  COUNT(DISTINCT s.speech_id) as speeches
FROM raw_contributions rc
LEFT JOIN speech_parts sp ON rc.contribution_id = sp.contribution_id
LEFT JOIN speeches s ON sp.speech_id = s.speech_id
GROUP BY rc.agenda_item_id
ORDER BY unique_speakers DESC
LIMIT 15;

-- 9. Speaker consistency (who speaks on which topics?)
SELECT 
  m.name_english,
  COUNT(DISTINCT s.agenda_item_id) as agenda_items_spoken_on,
  COUNT(DISTINCT s.speech_id) as total_speeches
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
WHERE s.speech_id IS NOT NULL
GROUP BY m.member_id
ORDER BY agenda_items_spoken_on DESC
LIMIT 20;

-- 10. Contribution timing (when do speeches happen in meetings?)
SELECT 
  CASE
    WHEN CAST(strftime('%H', contribution_time) AS INT) < 12 THEN 'Morning (< 12:00)'
    WHEN CAST(strftime('%H', contribution_time) AS INT) < 17 THEN 'Afternoon (12:00-17:00)'
    ELSE 'Evening (> 17:00)'
  END as time_of_day,
  COUNT(*) as contributions,
  COUNT(DISTINCT member_id) as speakers
FROM raw_contributions
WHERE contribution_time IS NOT NULL
GROUP BY time_of_day
ORDER BY contributions DESC;
