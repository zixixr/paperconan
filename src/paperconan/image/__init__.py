"""Internal image asset, evidence, and diagnostic helpers."""


class ImageDependencyError(RuntimeError):
    """Raised when an explicitly requested image operation lacks its optional extra."""
