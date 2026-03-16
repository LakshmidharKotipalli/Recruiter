from abc import ABC, abstractmethod
from schemas.models import SourceQueries, RawProfile


class BaseIntegration(ABC):
    """Abstract base class for all external talent pool integrations."""

    def __init__(self, api_key: str, mock_mode: bool = True):
        self.api_key = api_key
        self.mock_mode = mock_mode

    @abstractmethod
    async def fetch(self, queries: SourceQueries) -> list[RawProfile]:
        """Fetch profiles using the given queries. Must be implemented by subclasses."""
        pass

    def _mock_profiles(self) -> list[RawProfile]:
        """Return mock profiles. Subclasses should override this."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _mock_profiles()"
        )
