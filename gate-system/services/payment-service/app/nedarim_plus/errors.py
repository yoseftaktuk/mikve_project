class NedarimError(Exception):
    """Nedarim Plus refused the request, or could not be reached at all."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
