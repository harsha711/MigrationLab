-- stage expand-add-column-and-triggers (expand)
ALTER TABLE account_user ADD COLUMN customer_note text;
CREATE OR REPLACE FUNCTION sync_note_to_customer_note() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        IF NEW.note IS DISTINCT FROM OLD.note THEN
            NEW.customer_note := NEW.note;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION sync_customer_note_to_note() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        IF NEW.customer_note IS DISTINCT FROM OLD.customer_note THEN
            NEW.note := NEW.customer_note;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_sync_note_to_customer_note BEFORE INSERT OR UPDATE OF note ON account_user FOR EACH ROW EXECUTE FUNCTION sync_note_to_customer_note();
CREATE TRIGGER trg_sync_customer_note_to_note BEFORE INSERT OR UPDATE OF customer_note ON account_user FOR EACH ROW EXECUTE FUNCTION sync_customer_note_to_note();
-- stage backfill-data (backfill) [repeat until 0 rows]
WITH batch AS (SELECT id FROM account_user WHERE note IS NOT NULL AND (customer_note IS DISTINCT FROM note) LIMIT 1000) UPDATE account_user SET customer_note = note FROM batch WHERE account_user.id = batch.id;
-- stage contract-drop-old-column (contract)
DROP TRIGGER trg_sync_note_to_customer_note ON account_user;
DROP TRIGGER trg_sync_customer_note_to_note ON account_user;
DROP FUNCTION sync_note_to_customer_note();
DROP FUNCTION sync_customer_note_to_note();
ALTER TABLE account_user DROP COLUMN note;
