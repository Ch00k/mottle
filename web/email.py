import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)


async def send_email(address: str, subject: str, plaintext_body: str, html_body: str) -> None:
    email = EmailMultiAlternatives(
        subject=subject,
        body=plaintext_body,
        from_email=f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM_EMAIL}>",
        to=[address],
    )
    email.attach_alternative(html_body, "text/html")

    await sync_to_async(email.send)()
    logger.debug(f"Email sent to {address}")
