from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderResult:
    success: bool
    provider_name: str
    error_message: str = ""


class EmailProvider(ABC):
    @abstractmethod
    def send(self, *, recipient: str, subject: str, body: str) -> ProviderResult: ...


class SmsProvider(ABC):
    @abstractmethod
    def send(self, *, recipient: str, body: str) -> ProviderResult: ...
