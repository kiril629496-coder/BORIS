from pathlib import Path

import proxy_pool as P


def test_intl_proxy_reads_rotated_runtime_env_without_restart(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PROXY_INTL_HOSTS=203.0.113.10:10000\n"
        "PROXY_INTL_USER=user-one\n"
        "PROXY_INTL_PASS=pass-one\n",
        encoding="utf-8",
    )
    old_path = P._INTL_ENV_PATH
    try:
        P._INTL_ENV_PATH = str(env)
        first = P.get_intl_requests_proxies()
        assert first["https"] == "http://user-one:pass-one@203.0.113.10:10000"

        env.write_text(
            "PROXY_INTL_HOSTS=203.0.113.20:10001\n"
            "PROXY_INTL_USER=user-two\n"
            "PROXY_INTL_PASS=pass-two\n",
            encoding="utf-8",
        )
        second = P.get_intl_requests_proxies()
        assert second["https"] == "http://user-two:pass-two@203.0.113.20:10001"
    finally:
        P._INTL_ENV_PATH = old_path


def test_intl_proxy_hot_reload_does_not_mutate_avito_proxy_globals(tmp_path):
    before = (P.PROXY_HOST, P.PROXY_USER, P.PROXY_PASS)
    env = tmp_path / ".env"
    env.write_text(
        "PROXY_INTL_HOSTS=203.0.113.30:10002\n"
        "PROXY_INTL_USER=rotated\n"
        "PROXY_INTL_PASS=rotated-pass\n",
        encoding="utf-8",
    )
    old_path = P._INTL_ENV_PATH
    try:
        P._INTL_ENV_PATH = str(env)
        assert P.get_intl_requests_proxies()
        assert (P.PROXY_HOST, P.PROXY_USER, P.PROXY_PASS) == before
    finally:
        P._INTL_ENV_PATH = old_path
