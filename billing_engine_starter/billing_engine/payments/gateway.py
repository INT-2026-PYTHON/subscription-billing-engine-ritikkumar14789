"""
PaymentGateway — abstract + two mock implementations.

In real life this would talk to Stripe / Razorpay / Adyen. For the project
we use mocks so tests are deterministic and the demo never hits the network.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from billing_engine.models import Invoice


@dataclass(frozen=True)
class PaymentResult:
    success: bool
    failure_reason: Optional[str] = None


class PaymentGateway(ABC):
    @abstractmethod
    def charge(self, invoice: Invoice) -> PaymentResult:
        raise NotImplementedError


class ScriptedGateway(PaymentGateway):
    """Returns pre-set results from a queue."""

    def __init__(self, results: list[PaymentResult]) -> None:
        self._results = list(results)
        self._index = 0

    def charge(self, invoice: Invoice) -> PaymentResult:
        if self._index >= len(self._results):
            raise IndexError("ScriptedGateway exhausted — no more results")
        result = self._results[self._index]
        self._index += 1
        return result


class FakeRandomGateway(PaymentGateway):
    """Succeeds at a configurable rate; seeded for reproducibility."""

    def __init__(self, success_rate: float = 0.7, seed: Optional[int] = None) -> None:
        self.success_rate = success_rate
        self._rng = random.Random(seed)

    def charge(self, invoice: Invoice) -> PaymentResult:
        if self._rng.random() < self.success_rate:
            return PaymentResult(True)
        return PaymentResult(False, "RANDOM_FAILURE")
