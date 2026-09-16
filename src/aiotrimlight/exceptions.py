"""Exceptions raised by aiotrimlight."""


class TrimlightError(Exception):
    """Base exception for aiotrimlight."""


class TrimlightConnectionError(TrimlightError):
    """Raised when a controller cannot be reached."""


class TrimlightHTTPError(TrimlightError):
    """Raised when the controller returns a non-success HTTP status."""

    def __init__(self, status: int) -> None:
        """Initialize the error."""
        self.status = status
        super().__init__(f"controller returned HTTP status {status}")


class TrimlightProtocolError(TrimlightError):
    """Raised when a controller response is malformed."""


class TrimlightCommandError(TrimlightError):
    """Raised when a controller rejects a command."""

    def __init__(self, code: int, message: str | None) -> None:
        """Initialize the error."""
        self.code = code
        self.message = message
        detail = f": {message}" if message else ""
        super().__init__(f"controller returned error code {code}{detail}")


class TrimlightDiscoveryError(TrimlightError):
    """Raised when mDNS discovery properties are invalid."""
