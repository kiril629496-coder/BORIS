from app.services.asterisk_gateway import _mcn_trunk_prerequisites

cases = [
    ({"auth_mode":"registration","registrar":"sip.mcn.ru","username":"u","password":"p","source_ips":[]}, True, None),
    ({"auth_mode":"registration","registrar":"sip:mcn.example.ru","username":"u","password":"p","source_ips":["1.2.3.4"]}, True, None),
    ({"auth_mode":"registration","registrar":"sip.mcn.ru","username":"","password":"p","source_ips":[]}, False, "mcn_registration_credentials_missing"),
    ({"auth_mode":"registration","registrar":"","username":"u","password":"p","source_ips":[]}, False, "mcn_registrar_missing"),
    ({"auth_mode":"ip","registrar":"sip.mcn.ru","source_ips":["1.2.3.4/32"]}, True, None),
    ({"auth_mode":"ip","registrar":"sip.mcn.ru","source_ips":[]}, False, "mcn_source_ips_missing"),
]
for cfg, ready, reason in cases:
    out = _mcn_trunk_prerequisites(cfg)
    assert out["ready"] is ready, (cfg,out)
    if reason:
        assert out["reason"] == reason, (cfg,out)
print("MCN_TRUNK_PREREQUISITES_OK",{"cases":len(cases)})
