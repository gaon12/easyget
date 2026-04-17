import logging

def setup_logging(verbose: bool = False, quiet: bool = False):
    """
    Configure logging settings and suppress noise from third-party libraries.
    """
    if quiet:
        level = logging.ERROR
    else:
        level = logging.DEBUG if verbose else logging.INFO
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # If no handlers are configured, add a simple StreamHandler
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    
    # Suppress httpx and httpcore logging
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    else:
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
