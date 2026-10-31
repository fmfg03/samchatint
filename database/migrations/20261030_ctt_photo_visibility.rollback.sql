-- Objects stay outside the web root; removing the gate hides, never publishes.
BEGIN;
DROP FUNCTION public.ctt_photo_is_committed(text);
COMMIT;
