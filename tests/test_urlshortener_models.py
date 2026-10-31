import uuid

import pytest
from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase

from urlshortener.models import HASH_LENGTH, ShortURL, uuid_to_hash

pytestmark = pytest.mark.django_db


class TestUuidToHash:
    def test_generates_hash_from_uuid(self) -> None:
        """Test that uuid_to_hash generates a hash string from UUID."""
        test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = uuid_to_hash(test_uuid)

        assert isinstance(result, str)
        assert len(result) <= HASH_LENGTH
        assert all(c in "0123456789abcdefghijklmnopqrstuvwxyz" for c in result)

    def test_deterministic_for_same_uuid(self) -> None:
        """Test that a UUID produces the same hash in every process.

        The expected value is fixed, so a hash that depended on the process (as Python's own hash() of a string does)
        would fail here.
        """
        test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")

        assert uuid_to_hash(test_uuid) == "gjgy7i0"

    def test_different_uuids_produce_different_hashes(self) -> None:
        """Test that different UUIDs produce different hashes."""
        uuid1 = uuid.UUID("12345678-1234-5678-1234-567812345678")
        uuid2 = uuid.UUID("87654321-4321-8765-4321-876543218765")

        hash1 = uuid_to_hash(uuid1)
        hash2 = uuid_to_hash(uuid2)

        assert hash1 != hash2

    def test_respects_custom_length(self) -> None:
        """Test that custom length parameter is respected."""
        test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")

        hash_short = uuid_to_hash(test_uuid, length=5)
        hash_long = uuid_to_hash(test_uuid, length=10)

        assert len(hash_short) <= 5
        assert len(hash_long) <= 10

    def test_handles_zero_uuid(self) -> None:
        """Test that zero UUID is handled correctly."""
        zero_uuid = uuid.UUID("00000000-0000-0000-0000-000000000000")
        result = uuid_to_hash(zero_uuid)

        assert isinstance(result, str)
        assert len(result) <= HASH_LENGTH

    def test_handles_max_uuid(self) -> None:
        """Test that maximum UUID value is handled correctly."""
        max_uuid = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        result = uuid_to_hash(max_uuid)

        assert isinstance(result, str)
        assert len(result) <= HASH_LENGTH


@pytest.mark.asyncio
class TestShortURL(TestCase):
    async def test_create_shorturl(self) -> None:
        """Test creating a ShortURL instance."""
        url = "https://example.com/very/long/url"
        short_url = await ShortURL.objects.acreate(url=url)

        assert short_url.id is not None
        assert short_url.url == url
        assert short_url.hash != ""
        assert len(short_url.hash) == HASH_LENGTH
        assert short_url.created_at is not None
        assert short_url.updated_at is not None

    async def test_hash_generated_on_save(self) -> None:
        """Test that hash is automatically generated on save."""
        short_url = ShortURL(url="https://example.com/test")
        assert short_url.hash == ""

        await short_url.asave()

        assert short_url.hash != ""
        assert len(short_url.hash) == HASH_LENGTH

    async def test_hash_consistent_with_id(self) -> None:
        """Test that hash is derived from the UUID id."""
        url = "https://example.com/consistent"
        short_url = await ShortURL.objects.acreate(url=url)

        expected_hash = uuid_to_hash(short_url.id)
        assert short_url.hash == expected_hash

    async def test_url_must_be_unique(self) -> None:
        """Test that duplicate URLs raise IntegrityError."""
        url = "https://example.com/duplicate"
        await ShortURL.objects.acreate(url=url)

        with pytest.raises(IntegrityError):
            await ShortURL.objects.acreate(url=url)

    async def test_hash_must_be_unique(self) -> None:
        """Test that duplicate hashes raise IntegrityError."""
        url1 = "https://example.com/url1"
        short_url1 = await ShortURL.objects.acreate(url=url1)

        # Try to create another ShortURL with same hash
        url2 = "https://example.com/url2"
        short_url2 = ShortURL(url=url2)
        short_url2.hash = short_url1.hash

        with pytest.raises(IntegrityError):
            await short_url2.asave()

    async def test_shorten_creates_new_url(self) -> None:
        """Test that shorten() creates a new ShortURL."""
        url = "https://example.com/to/shorten"
        short_url = await ShortURL.shorten(url)

        assert short_url.url == url
        assert short_url.hash != ""
        assert short_url.id is not None

    async def test_shorten_returns_existing_url(self) -> None:
        """Test that shorten() returns existing ShortURL for duplicate URL."""
        url = "https://example.com/existing"
        short_url1 = await ShortURL.shorten(url)
        short_url2 = await ShortURL.shorten(url)

        assert short_url1.id == short_url2.id
        assert short_url1.hash == short_url2.hash
        assert short_url1.url == short_url2.url

    async def test_shorten_idempotent(self) -> None:
        """Test that calling shorten() multiple times doesn't create duplicates."""
        url = "https://example.com/idempotent"

        # Call shorten multiple times
        results = [await ShortURL.shorten(url) for _ in range(5)]

        # All should have the same ID
        ids = [r.id for r in results]
        assert len(set(ids)) == 1

        # Only one ShortURL should exist in database
        count = await ShortURL.objects.filter(url=url).acount()
        assert count == 1

    async def test_full_short_url_property(self) -> None:
        """Test that full_short_url property returns correct URL."""
        url = "https://example.com/test"
        short_url = await ShortURL.objects.acreate(url=url)

        expected = f"{settings.URLSHORTENER_BASE_URL}/{short_url.hash}"
        assert short_url.full_short_url == expected

    async def test_str_representation(self) -> None:
        """Test the string representation of ShortURL."""
        url = "https://example.com/test"
        short_url = await ShortURL.objects.acreate(url=url)

        str_repr = str(short_url)
        assert short_url.hash in str_repr
        assert short_url.url in str_repr
        assert "->" in str_repr

    async def test_timestamps_auto_populated(self) -> None:
        """Test that created_at and updated_at are automatically set."""
        url = "https://example.com/timestamps"
        short_url = await ShortURL.objects.acreate(url=url)

        assert short_url.created_at is not None
        assert short_url.updated_at is not None
        # created_at and updated_at should be very close on creation
        time_diff = (short_url.updated_at - short_url.created_at).total_seconds()
        assert time_diff < 1

    async def test_updated_at_changes_on_update(self) -> None:
        """Test that updated_at changes when model is updated."""
        url = "https://example.com/update-test"
        short_url = await ShortURL.objects.acreate(url=url)
        original_updated_at = short_url.updated_at

        # Update the model (change URL to trigger update)
        short_url.url = "https://example.com/updated-url"
        await short_url.asave()

        assert short_url.updated_at > original_updated_at

    async def test_can_query_by_hash(self) -> None:
        """Test that we can query ShortURL by hash."""
        url = "https://example.com/query-by-hash"
        created_short_url = await ShortURL.objects.acreate(url=url)

        found_short_url = await ShortURL.objects.aget(hash=created_short_url.hash)

        assert found_short_url.id == created_short_url.id
        assert found_short_url.url == url

    async def test_can_query_by_url(self) -> None:
        """Test that we can query ShortURL by URL."""
        url = "https://example.com/query-by-url"
        created_short_url = await ShortURL.objects.acreate(url=url)

        found_short_url = await ShortURL.objects.aget(url=url)

        assert found_short_url.id == created_short_url.id
        assert found_short_url.hash == created_short_url.hash

    async def test_handles_long_urls(self) -> None:
        """Test that very long URLs are handled correctly."""
        # Django URLField has default max_length of 200
        long_url = "https://example.com/" + "a" * 150
        short_url = await ShortURL.objects.acreate(url=long_url)

        assert short_url.url == long_url
        assert len(short_url.hash) == HASH_LENGTH

    async def test_handles_urls_with_query_params(self) -> None:
        """Test that URLs with query parameters are handled correctly."""
        url = "https://example.com/page?param1=value1&param2=value2"
        short_url = await ShortURL.objects.acreate(url=url)

        assert short_url.url == url
        assert short_url.hash != ""

    async def test_handles_urls_with_fragments(self) -> None:
        """Test that URLs with fragments are handled correctly."""
        url = "https://example.com/page#section"
        short_url = await ShortURL.objects.acreate(url=url)

        assert short_url.url == url
        assert short_url.hash != ""

    async def test_different_urls_get_different_hashes(self) -> None:
        """Test that different URLs get different short hashes."""
        url1 = "https://example.com/url1"
        url2 = "https://example.com/url2"

        short_url1 = await ShortURL.objects.acreate(url=url1)
        short_url2 = await ShortURL.objects.acreate(url=url2)

        assert short_url1.hash != short_url2.hash
        assert short_url1.id != short_url2.id
