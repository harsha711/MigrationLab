BEGIN;
--
-- Rename field note on user to customer_note
--
ALTER TABLE "account_user" RENAME COLUMN "note" TO "customer_note";
COMMIT;
