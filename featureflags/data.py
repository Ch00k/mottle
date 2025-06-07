from . import models


class FeatureFlag:
    @staticmethod
    async def events_enabled_for_user(user_id: str) -> bool:
        events_enabled_for_users: list[str] | None = await models.FeatureFlag.objects.get_value(  # pyright: ignore
            "events_enabled_for_spotify_user_ids"
        )
        return events_enabled_for_users is not None and user_id in events_enabled_for_users

    @staticmethod
    async def concurrent_event_sources_fetching() -> bool:
        return await models.FeatureFlag.objects.get_value("concurrent_event_sources_fetching") is True  # pyright: ignore

    @staticmethod
    async def concurrent_events_fetching() -> bool:
        return await models.FeatureFlag.objects.get_value("concurrent_events_fetching") is True  # pyright: ignore
