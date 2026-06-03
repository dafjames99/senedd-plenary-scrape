-- Members & Contributors Queries
-- Analyze member activity, roles, and languages

-- 1. Most active speakers (by number of speeches)
SELECT 
  m.member_id,
  m.name_english,
  m.job_title_english,
  COUNT(s.speech_id) as num_speeches,
  SUM(LENGTH(s.speech_text)) as total_chars,
  ROUND(AVG(LENGTH(s.speech_text))) as avg_speech_length
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
GROUP BY m.member_id
ORDER BY num_speeches DESC
LIMIT 20;

-- 2. Members by language preference
SELECT 
  m.name_english,
  m.job_title_english,
  SUM(CASE WHEN s.language_detected = 'CY' THEN 1 ELSE 0 END) as welsh_speeches,
  SUM(CASE WHEN s.language_detected = 'EN' THEN 1 ELSE 0 END) as english_speeches,
  COUNT(s.speech_id) as total_speeches
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
GROUP BY m.member_id
ORDER BY total_speeches DESC
LIMIT 20;

-- 3. Members with specific roles (e.g., Cabinet Members)
SELECT 
  member_id,
  name_english,
  job_title_english
FROM members
WHERE job_title_english LIKE '%Minister%'
   OR job_title_english LIKE '%Cabinet%'
ORDER BY name_english;

-- 4. All contributions by a specific member (trace to speeches)
SELECT 
  rc.contribution_order_id,
  rc.contribution_type,
  rc.contribution_language,
  LENGTH(rc.contribution_verbatim) as raw_length,
  s.speech_id,
  COUNT(DISTINCT sp.contribution_id) as parts_in_speech
FROM raw_contributions rc
LEFT JOIN speech_parts sp ON rc.contribution_id = sp.contribution_id
LEFT JOIN speeches s ON sp.speech_id = s.speech_id
WHERE rc.member_id = 5053  -- Example: Huw Irranca-Davies
GROUP BY rc.contribution_id
ORDER BY rc.contribution_order_id;

-- 5. Member metadata
SELECT 
  member_id,
  name_english,
  job_title_english,
  job_title_welsh,
  sort_code,
  SUBSTR(biography_english, 1, 100) as bio_preview
FROM members
WHERE member_id IN (5053, 2717, 12157, 5030)
ORDER BY sort_code;

-- 6. Llywydd (Chair) statements
SELECT 
  m.name_english,
  m.job_title_english,
  COUNT(s.speech_id) as chair_statements
FROM members m
LEFT JOIN speeches s ON m.member_id = s.member_id
WHERE LOWER(m.job_title_english) LIKE '%llywydd%'
GROUP BY m.member_id;
