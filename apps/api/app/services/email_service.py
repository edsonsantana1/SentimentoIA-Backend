import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Envio de e-mails transacionais via SMTP configuravel."""

    @staticmethod
    def smtp_configured() -> bool:
        host = settings.SMTP_HOST.strip()
        from_email = settings.SMTP_EFFECTIVE_FROM_EMAIL
        username = settings.SMTP_EFFECTIVE_USERNAME
        password = settings.SMTP_PASSWORD.strip()

        if (username and not password) or (password and not username):
            logger.warning("SMTP configurado parcialmente: usuario/senha inconsistentes")
            return False

        return bool(host and from_email)

    @staticmethod
    async def send_password_reset_email(*, to_email: str, to_name: str | None, reset_url: str) -> bool:
        subject = "Redefinicao de senha - SentimentoIA"
        greeting = f"Ola, {to_name}" if to_name else "Ola"
        text_body = (
            f"{greeting}.\n\n"
            "Recebemos uma solicitacao para redefinir sua senha.\n"
            f"Use este link para continuar: {reset_url}\n\n"
            "Se voce nao solicitou esta alteracao, ignore este e-mail.\n\n"
            "Equipe SentimentoIA"
        )
        html_body = (
            f"<p>{greeting}.</p>"
            "<p>Recebemos uma solicitacao para redefinir sua senha.</p>"
            f"<p><a href=\"{reset_url}\">Clique aqui para redefinir sua senha</a></p>"
            "<p>Se voce nao solicitou esta alteracao, ignore este e-mail.</p>"
            "<p>Equipe SentimentoIA</p>"
        )

        if not EmailService.smtp_configured():
            logger.warning("SMTP nao configurado. Link de reset sera registrado nos logs para ambiente de desenvolvimento.")
            logger.info("Password reset link (%s): %s", to_email, reset_url)
            return False

        try:
            await asyncio.to_thread(
                EmailService._send_via_smtp,
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
            )
            logger.info("E-mail de reset enviado para: %s", to_email)
            return True
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            logger.exception("Falha ao enviar e-mail de reset (%s): %s", to_email, exc)
            return False

    @staticmethod
    def _send_via_smtp(*, to_email: str, subject: str, text_body: str, html_body: str) -> None:
        msg = EmailMessage()
        from_name = settings.SMTP_FROM_NAME.strip()
        from_email = settings.SMTP_EFFECTIVE_FROM_EMAIL
        username = settings.SMTP_EFFECTIVE_USERNAME
        password = settings.SMTP_PASSWORD.strip()

        if not from_email:
            raise ValueError("SMTP_FROM nao configurado")

        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        host = settings.SMTP_HOST.strip()
        port = int(settings.SMTP_PORT)

        if settings.SMTP_USE_SSL:
            smtp_client = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            smtp_client = smtplib.SMTP(host, port, timeout=15)

        with smtp_client as server:
            if not settings.SMTP_USE_SSL and settings.SMTP_USE_TLS:
                server.ehlo()
                server.starttls()
                server.ehlo()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
