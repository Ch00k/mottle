"""
ASGI config for mottle project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mottle.settings")

application = get_asgi_application()


# TODO: This must be used with gunicorn
# class LifespanlessUvicornWorker(UvicornWorker):
#     """
#     https://stackoverflow.com/questions/75217343/django-can-only-handle-asgi-http-connections-not-lifespan-in-uvicorn

#     Generate UvicornWorker with lifespan='off', because Django does not
#     (and probably will not, as per https://code.djangoproject.com/ticket/31508)
#     support Lifespan.
#     """

#     def __init__(self, *args: Any, **kwargs: Any) -> None:
#         super().__init__(*args, **kwargs)
#         self.config.lifespan = "off"
