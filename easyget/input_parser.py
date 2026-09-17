import csv
import logging
import os

from .utils import _sanitize_filename, get_filename_from_url

logger = logging.getLogger(__name__)


def parse_file_list(file_path: str) -> list[tuple[str, str]]:
    """
    Parse an input file (txt, csv, or tsv) to extract a list of (URL, filename) tuples.
    """
    file_list: list[tuple[str, str]] = []
    ext = os.path.splitext(file_path)[1].lower()

    try:
        with open(file_path, encoding="utf-8") as f:
            if ext == ".txt":
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        file_list.append((stripped, get_filename_from_url(stripped)))
            elif ext in [".csv", ".tsv"]:
                delimiter = "," if ext == ".csv" else "\t"
                reader = csv.DictReader(f, delimiter=delimiter)
                if reader.fieldnames and "url" not in reader.fieldnames:
                    logger.warning(
                        f"'{file_path}' has no 'url' column; using the first column "
                        f"({reader.fieldnames[0]!r}) as URLs."
                    )
                for row in reader:
                    url_val = row.get("url")
                    if not url_val:
                        # Fallback if no 'url' header exists, try first column
                        url_val = next(iter(row.values()))

                    if not url_val:
                        continue

                    filename_val = row.get("filename")
                    if filename_val:
                        # Sanitize user-supplied names — a hostile csv could
                        # otherwise write outside the output directory.
                        filename_val = _sanitize_filename(filename_val.strip())
                        if filename_val is None:
                            logger.warning(
                                f"Ignoring unsafe filename in '{file_path}' row; "
                                "deriving it from the URL instead."
                            )
                    if not filename_val:
                        filename_val = get_filename_from_url(url_val)
                    file_list.append((url_val.strip(), filename_val))
            else:
                logger.error(
                    f"easyget error: Unsupported file format '{ext}'. Supported: txt, csv, tsv."
                )
    except Exception as e:
        logger.error(f"easyget error: Failed to parse file list '{file_path}': {e}")

    return file_list
