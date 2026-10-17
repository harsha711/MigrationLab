-- stage expand (expand)
ALTER TABLE account_user ADD COLUMN customer_note text NULL;
CREATE OR REPLACE FUNCTION migproto_note_sync() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.customer_note IS NULL THEN
      NEW.customer_note := NEW.note;
    ELSIF NEW.note IS NULL THEN
      NEW.note := NEW.customer_note;
    END IF;
  ELSIF NEW.note IS DISTINCT FROM OLD.note
        AND NEW.customer_note IS NOT DISTINCT FROM OLD.customer_note THEN
    NEW.customer_note := NEW.note;
  ELSIF NEW.customer_note IS DISTINCT FROM OLD.customer_note THEN
    NEW.note := NEW.customer_note;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER migproto_note_sync BEFORE INSERT OR UPDATE ON account_user FOR EACH ROW EXECUTE FUNCTION migproto_note_sync();
-- stage backfill (backfill) [repeat until 0 rows]
UPDATE account_user SET customer_note = note WHERE id IN (SELECT id FROM account_user WHERE customer_note IS DISTINCT FROM note ORDER BY id LIMIT 100);
-- stage contract (contract)
DROP TRIGGER IF EXISTS migproto_note_sync ON account_user;
DROP FUNCTION IF EXISTS migproto_note_sync();
ALTER TABLE account_user DROP COLUMN note;
