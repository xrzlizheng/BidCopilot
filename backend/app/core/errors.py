class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "application error", *, code: str | None = None) -> None:
        self.message = message
        if code is not None:
            self.code = code
        super().__init__(message)


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class QuotaExceededError(AppError):
    status_code = 429
    code = "quota_exceeded"
