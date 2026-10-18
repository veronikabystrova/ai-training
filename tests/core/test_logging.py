import json

import structlog

from app.core.logging import configure_logging, redact_processor


def test_redact_drops_credential_keys_at_any_depth() -> None:
    event = {
        "event": "x",
        "Password": "p",
        "reset_token": "t",
        "Authorization": "Bearer a",
        "client_secret": "s",
        "user_id": "u",
        "nested": {"token_id": "1", "keep": 2, "deep": [{"password_hash": "h", "ok": 1}]},
    }
    out = redact_processor(None, "info", event)
    assert out == {"event": "x", "user_id": "u", "nested": {"keep": 2, "deep": [{"ok": 1}]}}


def test_configured_logger_emits_redacted_json(capsys: object) -> None:
    configure_logging()
    structlog.get_logger("t").info("hello", password="p", user_id="u")
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    line = json.loads(out.strip().splitlines()[-1])
    assert line["event"] == "hello"
    assert line["user_id"] == "u"
    assert "password" not in line
