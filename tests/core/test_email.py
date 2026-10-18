from structlog.testing import capture_logs

from app.core.email import LoggingEmailSender


async def test_logging_sender_logs_template_only() -> None:
    with capture_logs() as logs:
        await LoggingEmailSender().send(
            to="a@example.com", template="verify", context={"token": "secret-token"}
        )
    assert logs == [{"event": "email.send", "template": "verify", "log_level": "info"}]
