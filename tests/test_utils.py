import copy
import json
import os
from base64 import b64encode

import pytest
from django.conf import settings
from django.contrib.gis.geos import Point

from web.images import calculate_base64_size
from web.models import EventUpdateChangesJSONDecoder, EventUpdateChangesJSONEncoder


@pytest.mark.parametrize("input_size", [1, 2, 3, 4, 40, 41, 42, 43])
def test_calculate_base64_size(input_size: int) -> None:
    input = os.urandom(input_size)  # noqa: A001
    assert calculate_base64_size(input) == len(b64encode(input))


def test_custom_json_encoder_decoder() -> None:
    data = {
        "stream_urls": ["http://example.com/stream"],
        "tickets_urls": ["http://example.com/tickets"],
        "geolocation": Point(5, 23, srid=settings.GEODJANGO_SRID),
    }
    data_normalized = {
        "stream_urls": ["http://example.com/stream"],
        "tickets_urls": ["http://example.com/tickets"],
        "geolocation": [5.0, 23.0],
    }

    as_json = json.dumps(data, cls=EventUpdateChangesJSONEncoder)
    as_object = json.loads(as_json, cls=EventUpdateChangesJSONDecoder)

    as_object_normalized = copy.deepcopy(as_object)
    as_object_normalized["geolocation"] = list(as_object_normalized["geolocation"].coords)

    assert as_object_normalized == data_normalized
