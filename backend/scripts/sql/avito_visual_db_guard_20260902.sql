CREATE OR REPLACE FUNCTION boris_guard_legacy_avito_media_asset()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE historical_bad boolean;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM media_assets h
    WHERE h.account_id=NEW.account_id AND h.storage_key=NEW.storage_key AND h.id<>coalesce(NEW.id,0)
      AND (lower(coalesce(h.source_provider,'')) IN ('boris_deterministic','boris_unique') OR coalesce(h.source_ref,'')='campaign_openai_gpt_image_2')
  ) INTO historical_bad;
  IF NEW.deleted_at IS NULL AND (
       lower(coalesce(NEW.source_provider,'')) IN ('boris_deterministic','boris_unique')
       OR coalesce(NEW.source_ref,'')='campaign_openai_gpt_image_2'
       OR historical_bad
       OR position('fullai_' in lower(coalesce(NEW.storage_key,''))) > 0
       OR position('gptimg_' in lower(coalesce(NEW.storage_key,''))) > 0
       OR coalesce(NEW.storage_key,'') ~ 'banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}[.]png$'
  ) THEN
    RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: legacy/source-laundered active MediaAsset forbidden storage_key=%', NEW.storage_key USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_guard_legacy_avito_media_asset ON media_assets;
CREATE TRIGGER trg_boris_guard_legacy_avito_media_asset BEFORE INSERT OR UPDATE OF source_provider,source_ref,deleted_at,storage_key,account_id ON media_assets FOR EACH ROW EXECUTE FUNCTION boris_guard_legacy_avito_media_asset();

CREATE OR REPLACE FUNCTION boris_guard_legacy_avito_media_link()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE bad boolean;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM media_assets ma WHERE ma.id=NEW.media_id AND (
      ma.deleted_at IS NOT NULL OR lower(coalesce(ma.source_provider,'')) IN ('boris_deterministic','boris_unique') OR coalesce(ma.source_ref,'')='campaign_openai_gpt_image_2'
      OR position('fullai_' in lower(coalesce(ma.storage_key,''))) > 0 OR position('gptimg_' in lower(coalesce(ma.storage_key,''))) > 0
      OR coalesce(ma.storage_key,'') ~ 'banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}[.]png$'
      OR EXISTS (SELECT 1 FROM media_assets h WHERE h.account_id=ma.account_id AND h.storage_key=ma.storage_key AND h.id<>ma.id AND (lower(coalesce(h.source_provider,'')) IN ('boris_deterministic','boris_unique') OR coalesce(h.source_ref,'')='campaign_openai_gpt_image_2'))
    )
  ) INTO bad;
  IF bad THEN RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: link to forbidden MediaAsset %', NEW.media_id USING ERRCODE='check_violation'; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_guard_legacy_avito_media_link ON media_links;
CREATE TRIGGER trg_boris_guard_legacy_avito_media_link BEFORE INSERT OR UPDATE OF media_id ON media_links FOR EACH ROW EXECUTE FUNCTION boris_guard_legacy_avito_media_link();

CREATE OR REPLACE FUNCTION boris_guard_canonical_avito_storage()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item jsonb; image_url text; rel text; bad boolean;
BEGIN
  IF coalesce(NEW.key,'') IN ('feed_items','drafts','feed_prefs','parsed_products','banner_showcase','banner_prompt_guide','posting_showcase_style') AND (position('fullai_' in lower(coalesce(NEW.value,''))) > 0 OR position('gptimg_' in lower(coalesce(NEW.value,''))) > 0) THEN RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: fullai image/reference forbidden account=% key=%', NEW.account_id, NEW.key USING ERRCODE='check_violation'; END IF;
  IF coalesce(NEW.key,'') NOT IN ('feed_items','drafts') THEN RETURN NEW; END IF;
  IF coalesce(NEW.value,'')='' THEN RETURN NEW; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(NEW.value::jsonb) LOOP
    IF jsonb_typeof(coalesce(item->'images','[]'::jsonb)) <> 'array' THEN CONTINUE; END IF;
    FOR image_url IN SELECT value FROM jsonb_array_elements_text(coalesce(item->'images','[]'::jsonb)) LOOP
      IF image_url ~ '/banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}[.]png([?].*)?$' THEN RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: legacy timestamp banner forbidden account=% key=% url=%', NEW.account_id, NEW.key, image_url USING ERRCODE='check_violation'; END IF;
      IF position('/images/' in image_url)=0 THEN CONTINUE; END IF;
      rel := split_part(split_part(image_url,'/images/',2),'?',1);
      SELECT EXISTS(
        SELECT 1 FROM media_assets ma WHERE ma.account_id=NEW.account_id AND ma.storage_key=rel AND (
          lower(coalesce(ma.source_provider,'')) IN ('boris_deterministic','boris_unique') OR coalesce(ma.source_ref,'')='campaign_openai_gpt_image_2'
          OR EXISTS (SELECT 1 FROM media_assets h WHERE h.account_id=ma.account_id AND h.storage_key=ma.storage_key AND h.id<>ma.id AND (lower(coalesce(h.source_provider,'')) IN ('boris_deterministic','boris_unique') OR coalesce(h.source_ref,'')='campaign_openai_gpt_image_2'))
        )
      ) INTO bad;
      IF bad THEN RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: legacy/source-laundered URL forbidden account=% key=% storage_key=%', NEW.account_id, NEW.key, rel USING ERRCODE='check_violation'; END IF;
    END LOOP;
  END LOOP;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_guard_canonical_avito_storage ON storage;
CREATE TRIGGER trg_boris_guard_canonical_avito_storage BEFORE INSERT OR UPDATE OF value,key,account_id ON storage FOR EACH ROW EXECUTE FUNCTION boris_guard_canonical_avito_storage();


CREATE OR REPLACE FUNCTION boris_guard_legacy_banner_row()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF lower(coalesce(NEW.source,''))='full_ai' OR position('fullai_' in lower(coalesce(NEW.filename,''))) > 0 OR position('gptimg_' in lower(coalesce(NEW.filename,''))) > 0 THEN
    RAISE EXCEPTION 'BORIS_AVITO_VISUAL_GUARD: legacy full-AI banner row forbidden account=% filename=% source=%', NEW.account_id, NEW.filename, NEW.source USING ERRCODE='check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_boris_guard_legacy_banner_row ON banners;
CREATE TRIGGER trg_boris_guard_legacy_banner_row BEFORE INSERT OR UPDATE OF source,filename,account_id ON banners FOR EACH ROW EXECUTE FUNCTION boris_guard_legacy_banner_row();
