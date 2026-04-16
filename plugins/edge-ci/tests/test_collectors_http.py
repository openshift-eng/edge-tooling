"""Tests for payload_monitor.collectors.http."""

from payload_monitor.collectors.http import TimeoutSession, create_session


class TestTimeoutSession:
    def test_default_timeout(self):
        session = TimeoutSession(default_timeout=15.0)
        assert session._default_timeout == 15.0


class TestCreateSession:
    def test_creates_session(self):
        session = create_session()
        assert session is not None

    def test_custom_parameters(self):
        session = create_session(retries=5, backoff_factor=1.0, default_timeout=60.0)
        assert session._default_timeout == 60.0

    def test_mounts_adapters(self):
        session = create_session()
        assert "https://" in session.adapters
        assert "http://" in session.adapters
