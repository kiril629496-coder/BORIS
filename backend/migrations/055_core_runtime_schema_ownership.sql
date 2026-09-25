BEGIN;

-- BORIS_CORE_RUNTIME_SCHEMA_OWNERSHIP_V1
-- Runtime code must never call SQLAlchemy create_all/drop_all. These legacy
-- core tables already exist in production; this migration makes that baseline
-- explicit and fails closed if an installation is incomplete.
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['public.accounts','public.storage','public.tasks']::text[]) x
   WHERE to_regclass(x) IS NULL;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'CORE_RUNTIME_SCHEMA missing relations: %', missing;
  END IF;

  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY[
      'id','account_id','name','password_hash','created_at','avito_client_id',
      'avito_client_secret','avito_user_id','avito_login','avito_password','comment',
      'company_website','company_niche','company_tone','company_description',
      'company_advantages','telegram_chat_id','owner_user_id','client_goal',
      'client_goal_text','billing_mode','reminder_enabled','reminder_stages',
      'reminder_delay_days','is_own','company_client_description'
    ]::text[]) x
   WHERE NOT EXISTS (
     SELECT 1 FROM information_schema.columns c
      WHERE c.table_schema='public' AND c.table_name='accounts' AND c.column_name=x
   );
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'CORE_RUNTIME_SCHEMA accounts missing columns: %', missing; END IF;

  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY['id','account_id','key','value']::text[]) x
   WHERE NOT EXISTS (
     SELECT 1 FROM information_schema.columns c
      WHERE c.table_schema='public' AND c.table_name='storage' AND c.column_name=x
   );
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'CORE_RUNTIME_SCHEMA storage missing columns: %', missing; END IF;

  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY[
      'id','account_id','task_type','status','payload','result','error_message',
      'created_at','updated_at','run_at','depends_on_task_id'
    ]::text[]) x
   WHERE NOT EXISTS (
     SELECT 1 FROM information_schema.columns c
      WHERE c.table_schema='public' AND c.table_name='tasks' AND c.column_name=x
   );
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'CORE_RUNTIME_SCHEMA tasks missing columns: %', missing; END IF;

  SELECT string_agg(x, ', ' ORDER BY x) INTO missing
    FROM unnest(ARRAY[
      'accounts_pkey','ix_accounts_account_id','ix_accounts_id','ix_accounts_owner_user_id',
      'storage_pkey','ix_storage_account_id','ix_storage_account_key_id_desc','ix_storage_id','ix_storage_key','uq_storage_core_singletons',
      'tasks_pkey','ix_tasks_account_id','ix_tasks_depends_on_task_id','ix_tasks_id','ix_tasks_task_type'
    ]::text[]) x
   WHERE to_regclass('public.' || x) IS NULL;
  IF missing IS NOT NULL THEN RAISE EXCEPTION 'CORE_RUNTIME_SCHEMA missing indexes: %', missing; END IF;
END $$;

COMMIT;
