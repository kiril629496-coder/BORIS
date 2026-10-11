ALTER TABLE internal_call_participants
  DROP CONSTRAINT IF EXISTS internal_call_participants_state_check;

ALTER TABLE internal_call_participants
  ADD CONSTRAINT internal_call_participants_state_check
  CHECK (state IN (
    'invited','ringing','accepted','connected',
    'left','declined','missed','failed','busy'
  ));
