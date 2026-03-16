"""Human-in-the-Loop checkpoint store.

Each active pipeline run has a HITLCheckpoint entry in the store.
The orchestrator awaits the checkpoint; the /api/run/{run_id}/resume endpoint resolves it.
"""

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HITLCheckpoint:
    """
    A single pause point in the pipeline.

    The orchestrator calls `await checkpoint.wait()` to block until
    the recruiter submits a decision via the resume endpoint.
    The resume endpoint calls `checkpoint.resolve(decision)` to unblock.
    """

    def __init__(self):
        self._event = asyncio.Event()
        self.decision: Optional[dict] = None

    def resolve(self, decision: dict) -> None:
        """Called by the resume endpoint to unblock the waiting orchestrator."""
        self.decision = decision
        self._event.set()

    async def wait(self, timeout: float = 300) -> Optional[dict]:
        """
        Block until resolved or timeout.

        Returns the decision dict, or None if timeout elapsed
        (caller should treat None as 'proceed with defaults').
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return self.decision
        except asyncio.TimeoutError:
            logger.warning("HITL checkpoint timed out — auto-proceeding with defaults.")
            return None


class HITLStore:
    """
    In-memory registry of active HITL checkpoints.
    Adequate for single-instance deployments; replace with Redis for multi-instance.
    """

    def __init__(self):
        self._store: dict[str, HITLCheckpoint] = {}

    def create(self, run_id: str) -> HITLCheckpoint:
        """Create and register a new checkpoint for this run."""
        cp = HITLCheckpoint()
        self._store[run_id] = cp
        return cp

    def get(self, run_id: str) -> Optional[HITLCheckpoint]:
        """Return the active checkpoint for a run, or None."""
        return self._store.get(run_id)

    def resolve(self, run_id: str, decision: dict) -> bool:
        """
        Resolve the checkpoint for a run with the given decision.
        Returns True if the checkpoint existed and was resolved, False otherwise.
        """
        cp = self._store.get(run_id)
        if cp is None:
            return False
        cp.resolve(decision)
        return True

    def delete(self, run_id: str) -> None:
        """Remove the checkpoint entry (call after pipeline completes)."""
        self._store.pop(run_id, None)


# Module-level singleton — shared across all requests in this process
_hitl_store: Optional[HITLStore] = None


def get_hitl_store() -> HITLStore:
    global _hitl_store
    if _hitl_store is None:
        _hitl_store = HITLStore()
    return _hitl_store
