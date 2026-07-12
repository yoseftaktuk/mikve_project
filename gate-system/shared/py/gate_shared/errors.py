from pydantic import BaseModel


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict | None = None


class AppError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details

