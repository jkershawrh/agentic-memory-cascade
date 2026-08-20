"""Base collector interface for data source adapters."""

from abc import ABC, abstractmethod
from typing import Iterator, List


class BaseCollector(ABC):
    name: str = "base"

    @abstractmethod
    def connect(self, config: dict) -> bool:
        ...

    @abstractmethod
    def collect(self) -> list:
        ...

    @abstractmethod
    def collect_all(self) -> list:
        ...

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": False}
