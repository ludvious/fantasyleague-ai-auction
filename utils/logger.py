import sys
from pathlib import Path
from loguru import logger

def setup_logger(
    log_level: str = "INFO",
    log_dir: str = "logs",
    log_to_file: bool = True,
    rotation: str = "10 MB",
    retention: str = "1 week",
    format_string: str = None
) -> None:
    """
    Args:
        log_level: (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log file
        log_to_file: if True, save logs su file
        rotation: (es. "10 MB", "1 day")
        retention:(es. "1 week", "30 days")
        format_string: Format custom log (None = default)
    """

    # remove default
    logger.remove()
    
    # default
    if format_string is None:
        format_string = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
    
    logger.add(
        sys.stdout,
        format=format_string,
        level=log_level,
        colorize=True,
        backtrace=True,
        diagnose=True
    )
    
    # Handler for file
    if log_to_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        logger.add(
            log_path / "fantacalcio_{time:YYYY-MM-DD}.log",
            format=format_string,
            level=log_level,
            rotation=rotation,
            retention=retention,
            compression="zip",
            backtrace=True,
            diagnose=True
        )
        
        logger.info(f"Logging su file abilitato: {log_path}")
    
    logger.info(f"Logger inizializzato - Livello: {log_level}")


if __name__ == "__main__":
    # Test logging
    setup_logger(log_level="DEBUG", log_to_file=True)
    
    logger.debug("Questo è un messaggio DEBUG")
    logger.info("Questo è un messaggio INFO")
    logger.success("Questo è un messaggio SUCCESS")
    logger.warning("Questo è un messaggio WARNING")
    logger.error("Questo è un messaggio ERROR")
    
    try:
        1 / 0
    except Exception as e:
        logger.exception("Esempio di exception logging")