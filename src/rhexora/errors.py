class RhexoraError(Exception):
    """Base error for RHEXORA operations."""


class ConfigurationError(RhexoraError):
    """A node configuration or peer record is invalid."""


class ProtocolError(RhexoraError):
    """A peer sent an invalid or unsupported protocol message."""


class AuthenticationError(RhexoraError):
    """A peer could not authenticate."""


class SecurityError(RhexoraError):
    """A peer identity or transport security check failed."""


class RemoteTaskError(RhexoraError):
    """A worker rejected or failed a task."""
