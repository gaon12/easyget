class EasyGetError(Exception):
    """Base exception for easyget."""
    pass

class DownloadError(EasyGetError):
    """Raised when a download fails."""
    pass

class IntegrityError(EasyGetError):
    """Raised when a download's integrity cannot be verified (e.g., server ignores Range)."""
    pass
