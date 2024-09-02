import os
from base64 import b64encode

import pytest

from web.images import calculate_base64_size


@pytest.mark.parametrize("input_size", [1, 2, 3, 4, 40, 41, 42, 43])
def test_calculate_base64_size(input_size: int) -> None:
    input = os.urandom(input_size)
    assert calculate_base64_size(input) == len(b64encode(input))
