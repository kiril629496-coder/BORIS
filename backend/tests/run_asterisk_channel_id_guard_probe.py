from app.services.asterisk_gateway import _valid_asterisk_channel_id

valid=[
    "qa-mcn-answer-outer-v1;2",
    "qa-mcn-answer-outer-v1;1",
    "1788552723.42",
    "carrier_call:abc-123_def",
]
invalid=[
    "",
    "short",
    "../etc/passwd",
    "abc/defgh",
    "abc defgh",
    "abc?defgh",
    "a"*201,
]
for value in valid:
    assert _valid_asterisk_channel_id(value), value
for value in invalid:
    assert not _valid_asterisk_channel_id(value), value
print("ASTERISK_CHANNEL_ID_GUARD_OK",{"valid":len(valid),"invalid":len(invalid)})
