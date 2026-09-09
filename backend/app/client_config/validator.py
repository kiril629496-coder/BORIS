from app.client_config import schema as S

def _finding(field, code, severity, msg, blocking):
    return {"field": field, "code": code, "severity": severity,
            "human_message": msg, "blocking": blocking}

def _val(node):
    if isinstance(node, dict) and node.get("state") == S.KNOWN:
        return node.get("value")
    return None

def validate(config, owner_kind="user"):
    out = []
    if config.get("schema_version") != S.SCHEMA_VERSION:
        out.append(_finding("schema_version", "SCHEMA_VERSION_MISMATCH", "error",
                            "Config schema version is not supported", True))
    for sec in S.SECTIONS:
        if sec not in config:
            out.append(_finding(sec, "SECTION_MISSING", "error",
                                "Required section is absent", True))
    if owner_kind not in S.SUPPORTED_OWNER_KINDS:
        out.append(_finding("billing_owner_kind", "UNSUPPORTED_OWNER_KIND", "error",
                            "Owner kind is not supported yet", True))
    for dotted, (feature, on_missing) in S.FIELDS.items():
        node = S.get_field(config, dotted)
        if node is None:
            out.append(_finding(dotted, "FIELD_ABSENT", "error",
                                "Field was not collected at all", True))
            continue
        if node.get("state") == S.UNKNOWN:
            if on_missing.startswith("block:"):
                out.append(_finding(dotted, "BLOCKS_FEATURE", "error",
                                    "Unknown value blocks feature: " + on_missing[6:], True))
            elif on_missing == "needs_input":
                out.append(_finding(dotted, "NEEDS_INPUT", "info",
                                    "Value must be provided by the client", False))
            else:
                out.append(_finding(dotted, "DEFAULT_APPLIES", "info",
                                    "Platform default will be used", False))
    for f in ("kpi.target_leads_per_day", "kpi.max_cost_per_lead_rub",
              "kpi.daily_budget_limit_rub"):
        v = _val(S.get_field(config, f))
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            out.append(_finding(f, "MUST_BE_POSITIVE", "error",
                                "Value must be greater than zero", True))
    mode = _val(S.get_field(config, "automation.mode"))
    if mode is not None and mode not in S.ALLOWED_AUTOMATION_MODES:
        out.append(_finding("automation.mode", "BAD_ENUM", "error",
                            "Unknown automation mode", True))
    if mode == "autopilot" and not _val(S.get_field(config, "automation.money_actions")):
        out.append(_finding("automation.money_actions", "MANDATE_REQUIRED", "error",
                            "Autopilot requires an active money mandate", True))
    for f, cap in S.SAFETY_CAPS.items():
        v = _val(S.get_field(config, f))
        if v is not None and isinstance(v, (int, float)) and v > cap:
            out.append(_finding(f, "ABOVE_SAFETY_CAP", "error",
                                "Value exceeds platform safety cap", True))
    for name, node in (config.get("schedule") or {}).items():
        v = _val(node)
        if isinstance(v, dict) and v.get("cadence") not in (None,) + S.ALLOWED_CADENCE:
            out.append(_finding("schedule." + name, "BAD_CADENCE", "error",
                                "Cadence is not in the allowed policy list", True))
        if isinstance(v, str) and ("*" in v or "cron" in v.lower()):
            out.append(_finding("schedule." + name, "RAW_CRON_FORBIDDEN", "error",
                                "Raw cron expressions are not allowed in config", True))
    if _val(S.get_field(config, "channels.avito")) is None:
        out.append(_finding("channels.avito", "NO_CONNECTION", "error",
                            "Avito connection reference is required", True))
    return out

def status_of(findings):
    if any(f["blocking"] for f in findings if f["code"] != "NEEDS_INPUT"):
        return "invalid"
    if any(f["code"] == "NEEDS_INPUT" for f in findings):
        return "needs_input"
    return "valid"
