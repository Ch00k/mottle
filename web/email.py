import logging

from django.conf import settings
from httpx import AsyncClient, Response, Timeout

URL = "https://api.mailersend.com/v1/email"
HEADERS = {"Authorization": f"Bearer {settings.MAILERSEND_API_TOKEN}"}

logger = logging.getLogger(__name__)


async def send_email(address: str, subject: str, body: str) -> Response:
    mail_from = {"email": settings.MAIL_FROM_EMAIL, "name": settings.MAIL_FROM_NAME}
    mail_to = [{"email": address}]

    request_body = {"from": mail_from, "to": mail_to, "subject": subject, "text": body}

    client = AsyncClient(timeout=Timeout(settings.MAILERSEND_HTTP_TIMEOUT))
    resp = await client.post(URL, headers=HEADERS, json=request_body)
    resp.raise_for_status()
    return resp
