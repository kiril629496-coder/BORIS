\echo '--- users после миграции ---'
SELECT id, role, is_active, email_verified, status, email_normalized,
       trial_started_at, subscription_expires_at
  FROM users ORDER BY id;

\echo '--- сводка ---'
SELECT count(*) AS users_total,
       count(*) FILTER (WHERE email_verified) AS verified,
       count(*) FILTER (WHERE status = 'active') AS active,
       count(*) FILTER (WHERE status = 'pending_verification') AS pending,
       count(*) FILTER (WHERE status = 'blocked') AS blocked,
       count(*) FILTER (WHERE email_normalized IS NULL) AS norm_missing,
       count(*) FILTER (WHERE trial_started_at IS NOT NULL) AS trial_marked
  FROM users;

\echo '--- новые таблицы ---'
SELECT 'email_verifications' AS t, count(*) FROM email_verifications
UNION ALL SELECT 'auth_rate_events', count(*) FROM auth_rate_events
UNION ALL SELECT 'auth_blocks',      count(*) FROM auth_blocks;

\echo '--- новые индексы и констрейнты ---'
SELECT indexname FROM pg_indexes
 WHERE indexname IN ('ix_users_email_normalized','ix_evf_user_id','ix_evf_email_norm',
                     'ix_evf_created_at','ix_evf_active','ix_are_kind_key_created',
                     'ix_are_kind_ip_created','ix_ab_key_until')
 ORDER BY 1;
SELECT conname FROM pg_constraint WHERE conname = 'ck_users_status';
