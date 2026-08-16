"""Stable, machine-readable application errors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ApprenticeError(Exception):
    code: str
    message: str
    exit_code: int = 3

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ValidationError(ApprenticeError):
    def __init__(self, message: str, code: str = "VALIDATION_ERROR") -> None:
        super().__init__(code, message, 3)


class PolicyError(ApprenticeError):
    def __init__(self, message: str, code: str = "STOP_POLICY") -> None:
        super().__init__(code, message, 4)


class IntegrityError(ApprenticeError):
    def __init__(self, message: str, code: str = "INTEGRITY_ERROR") -> None:
        super().__init__(code, message, 5)


class NotFoundError(ApprenticeError):
    def __init__(self, message: str) -> None:
        super().__init__("NOT_FOUND", message, 3)
