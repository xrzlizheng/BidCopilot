from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SourceStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    CONTRACT_ERROR = "contract_error"


@dataclass(frozen=True, slots=True)
class DataError:
    code: str
    message: str
    field: str | None = None
    raw_payload_ref: str | None = None


class DataUnavailable(RuntimeError):
    def __init__(self, result: "DataResult") -> None:
        self.result = result
        super().__init__(f"{result.source}: {result.status}")


@dataclass(frozen=True, slots=True)
class DataResult[T]:
    status: SourceStatus
    data: T | None
    source: str
    fetched_at: datetime
    source_url: str | None = None
    error: DataError | None = None
    warnings: list[DataError] = field(default_factory=list)
    raw_ref: str | None = None
    order_no: str | None = None
    charged: bool = False
    api_calls: int = 0
    billing_units: int = 0

    @property
    def is_success(self) -> bool:
        return self.status in {SourceStatus.OK, SourceStatus.EMPTY}

    def unwrap(self) -> T:
        if not self.is_success or self.data is None:
            raise DataUnavailable(self)
        return self.data


def status_for_gateway_code(code: str) -> SourceStatus:
    if code == "100":
        return SourceStatus.OK
    if code == "110":
        return SourceStatus.EMPTY
    if code in {"104", "109"}:
        return SourceStatus.RATE_LIMITED
    if code in {"103", "105", "108"}:
        return SourceStatus.UNAUTHORIZED
    if code == "107":
        return SourceStatus.UNAVAILABLE
    return SourceStatus.CONTRACT_ERROR
