from abc import ABC, abstractmethod
from typing import List

from src.models.types import Alert


class Notifier(ABC):
    """Abstract base for alert delivery. Extend for Slack, Telegram, etc."""

    @abstractmethod
    def send(self, alerts: List[Alert]) -> None:
        ...
