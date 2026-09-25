SOURCE_GROUPS=(
 'freelance','tenders','jobs','startups','telegram','linkedin','communities','apps','maps','company_sites',
 'crm_companies','seo_agencies','design_studios','developers','new_companies','franchises','news','social','reviews','cold_b2b'
)
SOURCE_GROUP_SET=frozenset(SOURCE_GROUPS)


def validate_group(value):
    g=str(value or '').strip()
    if not g: return 'unmapped'
    if g not in SOURCE_GROUP_SET: raise RuntimeError('LEAD_RADAR_SOURCE_GROUP_UNKNOWN:'+g)
    return g


def source_coverage(connectors):
    groups=sorted({getattr(c,'group_key','unmapped') for c in connectors if getattr(c,'group_key','unmapped') in SOURCE_GROUP_SET})
    missing=[g for g in SOURCE_GROUPS if g not in groups]
    unmapped=sorted({getattr(c,'key','unknown') for c in connectors if getattr(c,'group_key','unmapped') not in SOURCE_GROUP_SET})
    return {
        'target_groups':len(SOURCE_GROUPS),
        'configured_groups':len(groups),
        'configured_group_keys':groups,
        'missing_group_keys':missing,
        'unmapped_connector_keys':unmapped,
        'coverage_ready':len(groups)==len(SOURCE_GROUPS),
    }
