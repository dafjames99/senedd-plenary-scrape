-- Admin & Sync Management Queries
-- Track pipeline execution, checkpoints, and synchronization

-- 1. View all sync checkpoints (ordered by recency)
SELECT 
  checkpoint_id,
  last_sync_date,
  last_meeting_id,
  file_count,
  status,
  notes,
  created_at
FROM sync_checkpoints
ORDER BY created_at DESC;

-- 2. Latest successful sync
SELECT 
  last_sync_date,
  last_meeting_id,
  file_count,
  status,
  notes
FROM sync_checkpoints
WHERE status = 'success'
ORDER BY created_at DESC
LIMIT 1;

-- 3. Sync history with gaps (find when syncs happened)
SELECT 
  checkpoint_id,
  last_sync_date,
  file_count,
  status,
  (SELECT last_sync_date FROM sync_checkpoints cp2 
   WHERE cp2.created_at < cp1.created_at 
   ORDER BY created_at DESC LIMIT 1) as previous_sync_date,
  julianday(last_sync_date) - julianday(
    COALESCE(
      (SELECT last_sync_date FROM sync_checkpoints cp2 
       WHERE cp2.created_at < cp1.created_at 
       ORDER BY created_at DESC LIMIT 1),
      date('2000-01-01')
    )
  ) as days_since_previous
FROM sync_checkpoints cp1
ORDER BY created_at DESC;

-- 4. Sync summary statistics
SELECT 
  COUNT(*) as total_syncs,
  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed,
  SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) as partial,
  SUM(file_count) as total_files_processed,
  ROUND(AVG(file_count)) as avg_files_per_sync
FROM sync_checkpoints;

-- 5. Meetings by sync period (which sync added which meetings?)
SELECT 
  sc.checkpoint_id,
  sc.last_sync_date,
  COUNT(DISTINCT m.meeting_id) as meetings_before_this_sync,
  sc.file_count as files_in_sync,
  sc.status
FROM sync_checkpoints sc
LEFT JOIN meetings m ON m.meeting_date <= sc.last_sync_date
GROUP BY sc.checkpoint_id
ORDER BY sc.last_sync_date DESC;

-- 6. Data growth over time (meetings added per sync)
SELECT 
  sc.checkpoint_id,
  DATE(sc.last_sync_date) as sync_date,
  (SELECT COUNT(*) FROM meetings WHERE meeting_date <= sc.last_sync_date) as cumulative_meetings,
  sc.file_count as files_added
FROM sync_checkpoints sc
WHERE sc.status = 'success'
ORDER BY sc.created_at DESC
LIMIT 10;

-- 7. Failed or partial syncs (for troubleshooting)
SELECT 
  checkpoint_id,
  last_sync_date,
  status,
  notes,
  file_count
FROM sync_checkpoints
WHERE status IN ('error', 'partial')
ORDER BY created_at DESC;

-- 8. Last known state (for recovery)
SELECT 
  'Last sync' as metric,
  last_sync_date as value
FROM sync_checkpoints
WHERE status = 'success'
ORDER BY created_at DESC
LIMIT 1
UNION ALL
SELECT 
  'Last meeting added',
  MAX(meeting_date)
FROM meetings
UNION ALL
SELECT 
  'Total meetings in DB',
  COUNT(*)
FROM meetings
UNION ALL
SELECT 
  'Total contributions in DB',
  COUNT(*)
FROM raw_contributions;

-- 9. Sync duration calculation (if timestamps available)
SELECT 
  checkpoint_id,
  last_sync_date,
  created_at,
  CAST((julianday(created_at) - julianday(last_sync_date)) * 86400 AS INT) as sync_duration_seconds,
  file_count,
  ROUND(1.0 * file_count / (CAST((julianday(created_at) - julianday(last_sync_date)) * 86400 AS INT) + 1)) as files_per_second
FROM sync_checkpoints
WHERE status = 'success'
  AND created_at > last_sync_date  -- Only if created_at is after sync_date
ORDER BY created_at DESC;

-- 10. Status code frequency
SELECT 
  status,
  COUNT(*) as occurrences,
  ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM sync_checkpoints), 1) as percent
FROM sync_checkpoints
GROUP BY status
ORDER BY occurrences DESC;
