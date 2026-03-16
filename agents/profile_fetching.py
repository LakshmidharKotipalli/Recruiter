import asyncio
import logging
from typing import Any
from schemas.models import QuerySet, RawProfile

logger = logging.getLogger(__name__)


class ProfileFetchingAgent:
    """Sub-Agent 03: Profile Fetching Agent.

    Dispatches queries concurrently to all integration sources using asyncio.gather.
    Handles per-source failures gracefully — if a source raises an exception,
    logs it and continues with partial results.
    """

    def __init__(self, integrations: dict[str, Any]):
        self.integrations = integrations

    async def fetch_all(
        self, query_set: QuerySet
    ) -> tuple[list[RawProfile], dict[str, bool]]:
        """Fetch profiles from all sources concurrently.

        Returns:
            Tuple of (all_raw_profiles, source_availability_map)
        """
        source_queries = {
            "google_cse": query_set.google_cse,
            "naukri": query_set.naukri,
            "pdl": query_set.pdl,
        }

        tasks = []
        source_names = []
        for source_name, queries in source_queries.items():
            integration = self.integrations.get(source_name)
            if integration is None:
                logger.warning(f"No integration registered for source: {source_name}")
                continue
            tasks.append(self._fetch_source(source_name, integration, queries))
            source_names.append(source_name)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_profiles: list[RawProfile] = []
        source_availability: dict[str, bool] = {}

        for source_name, result in zip(source_names, results):
            if isinstance(result, BaseException):
                logger.error(
                    f"Source '{source_name}' raised exception: {type(result).__name__}: {result}"
                )
                source_availability[source_name] = False
                continue

            profiles, success = result
            source_availability[source_name] = success
            if profiles:
                all_profiles.extend(profiles)
            logger.info(
                f"Source '{source_name}': {'OK' if success else 'FAILED'} — {len(profiles)} profiles"
            )

        logger.info(
            f"Profile fetching complete. Total raw profiles: {len(all_profiles)}"
        )
        return all_profiles, source_availability

    async def _fetch_source(
        self, source_name: str, integration: Any, queries: Any
    ) -> tuple[list[RawProfile], bool]:
        """Fetch profiles from a single source, catching all exceptions."""
        try:
            profiles = await integration.fetch(queries)
            return profiles, True
        except Exception as e:
            logger.error(f"Source '{source_name}' fetch failed: {type(e).__name__}: {e}")
            return [], False
