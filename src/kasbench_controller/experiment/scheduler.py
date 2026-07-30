"""Trial scheduler for generating randomized experiment schedules."""

from __future__ import annotations

import os
import random

import structlog

from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.models import TrialAssignment


class TrialScheduler:
    """Generates a randomized trial-to-autoscaler schedule for an experiment.

    The scheduler builds a pool of autoscaler entries (each repeated
    trials_per_autoscaler times), applies a Fisher-Yates shuffle using
    a seeded RNG, and assigns sequential trial identifiers.
    """

    def __init__(self, config: ExperimentConfig, logger: structlog.BoundLogger | None = None) -> None:
        self._config = config
        self._logger = logger or structlog.get_logger()
        self._effective_seed = self._resolve_seed()

    @property
    def effective_seed(self) -> int:
        """The seed used for randomization (provided or auto-generated)."""
        return self._effective_seed

    def generate_schedule(self) -> list[TrialAssignment]:
        """Generate the full ordered list of trial assignments.

        Returns a list of TrialAssignment(trial_identifier, autoscaler)
        in randomized order, where each autoscaler entry appears exactly
        trials_per_autoscaler times.
        """
        # Build the pool: each autoscaler entry repeated trials_per_autoscaler times
        pool: list[str] = []
        for autoscaler in self._config.autoscalers:
            pool.extend([autoscaler] * self._config.trials_per_autoscaler)

        # Fisher-Yates shuffle using the seeded RNG
        rng = random.Random(self._effective_seed)
        # random.Random.shuffle implements Fisher-Yates (Knuth) shuffle
        rng.shuffle(pool)

        # Generate trial assignments with sequential identifiers
        assignments: list[TrialAssignment] = []
        for i, autoscaler in enumerate(pool, start=1):
            trial_id = f"{self._config.trial_prefix}{i:04d}"
            assignments.append(
                TrialAssignment(
                    trial_identifier=trial_id,
                    autoscaler=autoscaler,
                    sequence_number=i,
                )
            )

        return assignments

    def _resolve_seed(self) -> int:
        """Resolve the effective seed: use provided or generate from os.urandom."""
        if self._config.random_seed is not None:
            return self._config.random_seed

        # Generate a non-deterministic seed from os.urandom (8 bytes → 64-bit int)
        seed = int.from_bytes(os.urandom(8), byteorder="big")
        self._logger.info(
            "generated_random_seed",
            seed=seed,
            message="No random seed provided; generated non-deterministic seed.",
        )
        return seed
