-- Speech Reconstruction Queries
-- View how XML contributions are reconstructed into semantic speeches

-- 1. View speeches with their constituent parts
SELECT
    s.speech_id,
    m.name_english as speaker,
    s.agenda_item_english,
    COUNT(sp.contribution_id) as num_parts,
    LENGTH(s.speech_text) as text_length,
    s.speech_text
FROM
    speeches s
    LEFT JOIN speech_parts sp ON s.speech_id = sp.speech_id
    LEFT JOIN members m ON s.member_id = m.member_id
GROUP BY
    s.speech_id
ORDER BY s.speech_id
LIMIT 15;

-- 2. Find multi-part speeches (2+ contributions per speaker+agenda)
SELECT
    s.speech_id,
    m.name_english as speaker,
    s.agenda_item_english,
    COUNT(sp.contribution_id) as num_parts,
    GROUP_CONCAT(sp.contribution_id, ', ') as contribution_ids
FROM
    speeches s
    LEFT JOIN speech_parts sp ON s.speech_id = sp.speech_id
    LEFT JOIN members m ON s.member_id = m.member_id
GROUP BY
    s.speech_id
HAVING
    COUNT(sp.contribution_id) > 1
ORDER BY num_parts DESC;

-- 3. Trace a speech back to its raw contributions
-- Example: Speech ID 50
SELECT
    s.speech_id,
    sp.contribution_id,
    rc.contribution_order_id,
    rc.contribution_type,
    rc.contribution_language,
    SUBSTR(
        rc.contribution_verbatim,
        1,
        100
    ) as preview,
    cc.cleaned_verbatim as cleaned_preview
FROM
    speeches s
    LEFT JOIN speech_parts sp ON s.speech_id = sp.speech_id
    LEFT JOIN raw_contributions rc ON sp.contribution_id = rc.contribution_id
    LEFT JOIN clean_contributions cc ON rc.contribution_id = cc.contribution_id
WHERE
    s.speech_id = 50
ORDER BY rc.contribution_order_id;

-- 4. Reconstruction statistics
SELECT
    COUNT(*) as total_speeches,
    COUNT(DISTINCT member_id) as unique_speakers,
    COUNT(DISTINCT agenda_item_id) as unique_agendas,
    COUNT(DISTINCT meeting_id) as meetings,
    MIN(LENGTH(speech_text)) as shortest_speech,
    MAX(LENGTH(speech_text)) as longest_speech,
    ROUND(AVG(LENGTH(speech_text))) as avg_length
FROM speeches;

-- 5. Show how many speeches have each number of parts
SELECT part_count, COUNT(*) as speech_count
FROM (
        SELECT s.speech_id, COUNT(sp.contribution_id) as part_count
        FROM speeches s
            LEFT JOIN speech_parts sp ON s.speech_id = sp.speech_id
        GROUP BY
            s.speech_id
    )
GROUP BY
    part_count
ORDER BY part_count;

SELECT *
FROM speeches
    RIGHT JOIN (
        SELECT
            contribution_id, speech_part_id AS part_id, speech_id, count(*) OVER (
                PARTITION BY
                    speech_id
            ) AS total_parts
        FROM speech_parts
    ) AS sp ON speeches.speech_id = sp.speech_id
WHERE
    sp.total_parts = 6