-- Align DB call-state domain with authoritative BORIS Phone application state machine.
ALTER TABLE telephony_calls DROP CONSTRAINT IF EXISTS ck_telephony_calls_state;
ALTER TABLE telephony_calls ADD CONSTRAINT ck_telephony_calls_state CHECK (state IN ('new','connecting','ringing','answered','active','on_hold','transferring','transferred','ended','missed','rejected','busy','failed','cancelled')) NOT VALID;
ALTER TABLE telephony_calls VALIDATE CONSTRAINT ck_telephony_calls_state;
