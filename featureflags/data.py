from . import models


class FeatureFlag:
    @staticmethod
    def playlist_updates_schedule() -> str | None:
        return models.FeatureFlag.objects.get_value("playlist_updates_schedule")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def event_updates_schedule() -> str | None:
        return models.FeatureFlag.objects.get_value("event_updates_schedule")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def events_enabled_for_user(user_id: str) -> bool:
        events_enabled_for_users: list[str] | None = models.FeatureFlag.objects.get_value(  # pyright: ignore[reportAttributeAccessIssue]
            "events_enabled_for_spotify_user_ids"
        )
        return events_enabled_for_users is not None and user_id in events_enabled_for_users

    @staticmethod
    async def aevents_enabled_for_user(user_id: str) -> bool:
        events_enabled_for_users: list[str] | None = await models.FeatureFlag.objects.aget_value(  # pyright: ignore[reportAttributeAccessIssue]
            "events_enabled_for_spotify_user_ids"
        )
        return events_enabled_for_users is not None and user_id in events_enabled_for_users

    @staticmethod
    def event_sources_fetching_concurrency_limit() -> int | None:
        return models.FeatureFlag.objects.get_value("event_sources_fetching_concurrency_limit")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    async def aevent_sources_fetching_concurrency_limit() -> int | None:
        return await models.FeatureFlag.objects.aget_value("event_sources_fetching_concurrency_limit")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def event_fetching_concurrency_limit() -> int | None:
        return models.FeatureFlag.objects.get_value("event_fetching_concurrency_limit")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    async def aevent_fetching_concurrency_limit() -> int | None:
        return await models.FeatureFlag.objects.aget_value("event_fetching_concurrency_limit")  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    async def resolve_songkick_urls() -> bool:
        return await models.FeatureFlag.objects.aget_value("resolve_songkick_urls") is True  # pyright: ignore[reportAttributeAccessIssue]
