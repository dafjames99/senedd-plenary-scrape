-- Data Quality & Validation Queries
-- Check integrity, traceability, and data completeness

-- 1. Verify all speeches have parts (lineage check)
SELECT 
  'Orphaned speeches' as check_name,
  COUNT(*) as count
FROM speeches s
WHERE NOT EXISTS (
  SELECT 1 FROM speech_parts sp WHERE sp.speech_id = s.speech_id
);

-- 2. Verify all speech parts reference valid contributions
SELECT 
  'Orphaned speech parts' as check_name,
  COUNT(*) as count
FROM speech_parts sp
WHERE NOT EXISTS (
  SELECT 1 FROM raw_contributions rc WHERE rc.contribution_id = sp.contribution_id
);

-- 3. Check for contributions without classification
SELECT 
  'Unclassified contributions' as check_name,
  COUNT(*) as count
FROM raw_contributions rc
WHERE NOT EXISTS (
  SELECT 1 FROM classified_contributions cc WHERE cc.contribution_id = rc.contribution_id
);

-- 4. Check for empty speeches
SELECT 
  'Empty speech texts' as check_name,
  COUNT(*) as count
FROM speeches
WHERE speech_text IS NULL OR TRIM(speech_text) = '';

-- 5. Classification distribution
SELECT 
  cc.row_type,
  COUNT(*) as count,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM classified_contributions), 1) as percent
FROM classified_contributions cc
GROUP BY cc.row_type
ORDER BY count DESC;

-- 6. Data completeness report
SELECT 
  'Total contributions' as metric,
  COUNT(*) as value
FROM raw_contributions
UNION ALL
SELECT 
  'Contributions with member_id',
  COUNT(*)
FROM raw_contributions
WHERE member_id IS NOT NULL
UNION ALL
SELECT 
  'Contributions classified',
  COUNT(*)
FROM classified_contributions
UNION ALL
SELECT 
  'Contributions in speeches',
  COUNT(DISTINCT contribution_id)
FROM speech_parts
UNION ALL
SELECT 
  'Unique members',
  COUNT(DISTINCT member_id)
FROM members
UNION ALL
SELECT 
  'Reconstructed speeches',
  COUNT(*)
FROM speeches
UNION ALL
SELECT 
  'Procedural events',
  COUNT(*)
FROM procedural_events;

-- 7. Member metadata completeness
SELECT 
  'Members with English name' as check_name,
  COUNT(*) as value
FROM members
WHERE name_english IS NOT NULL AND name_english != ''
UNION ALL
SELECT 
  'Members with job title',
  COUNT(*)
FROM members
WHERE job_title_english IS NOT NULL
UNION ALL
SELECT 
  'Members with biography',
  COUNT(*)
FROM members
WHERE biography_english IS NOT NULL OR biography_welsh IS NOT NULL;

-- 8. Traceability summary
SELECT 
  'Speeches with all parts linked' as check_name,
  COUNT(DISTINCT s.speech_id) as value
FROM speeches s
WHERE EXISTS (
  SELECT 1 FROM speech_parts sp WHERE sp.speech_id = s.speech_id
);

-- 9. Language distribution
SELECT 
  contribution_language,
  COUNT(*) as contributions,
  COUNT(DISTINCT member_id) as speakers,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM raw_contributions), 1) as percent
FROM raw_contributions
WHERE contribution_language IS NOT NULL
GROUP BY contribution_language
ORDER BY contributions DESC;

-- 10. Null value check (data quality)
SELECT 
  'Contributions without meeting_id' as issue,
  COUNT(*) as count
FROM raw_contributions
WHERE meeting_id IS NULL
UNION ALL
SELECT 
  'Speeches without member_id',
  COUNT(*)
FROM speeches
WHERE member_id IS NULL
UNION ALL
SELECT 
  'Contributions without contribution_id',
  COUNT(*)
FROM raw_contributions
WHERE contribution_id IS NULL;
