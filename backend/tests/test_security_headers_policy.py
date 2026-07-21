from pathlib import Path


ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_nginx_configs_use_report_only_csp_before_enforcement():
    for path in ("frontend/nginx.conf", "deploy/easypanel/nginx.conf"):
        config = _read(path)
        assert "Content-Security-Policy-Report-Only" in config
        assert "default-src 'self'" in config
        assert "object-src 'none'" in config
        assert "frame-ancestors 'none'" in config
        assert "connect-src 'self' https: wss:" in config
        assert "Content-Security-Policy \"" not in config


def test_nginx_configs_emit_hsts_without_forcing_subdomains():
    for path in ("frontend/nginx.conf", "deploy/easypanel/nginx.conf"):
        config = _read(path)
        assert 'Strict-Transport-Security "max-age=31536000" always;' in config
        assert "includeSubDomains" not in config
