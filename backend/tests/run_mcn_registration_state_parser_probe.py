from app.services.asterisk_gateway import _parse_registration_state

cases = {
    "No objects found.": "missing",
    "Status: Registered": "registered",
    "Status: Rejected": "rejected",
    "Status: Unregistered": "unregistered",
    "Status: Stopped": "stopped",
    "Status: Request Sent": "pending",
    "mcn-reg-12/sip:x Registered": "registered",
}
for raw, expected in cases.items():
    got=_parse_registration_state(raw)
    assert got==expected,(raw,got,expected)
print("MCN_REGISTRATION_STATE_PARSER_OK",{"cases":len(cases)})
