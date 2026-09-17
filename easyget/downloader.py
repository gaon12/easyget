import email.utils
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC

from .exceptions import DownloadError, EasyGetError, IntegrityError
from .session import Session
from .utils import (
    ProgressBar,
    SpeedLimiter,
    get_filename_from_headers,
    get_filename_from_url,
    parse_speed,
    safe_rename,
    should_download_output,
)

logger = logging.getLogger(__name__)

# Constants / 상수
CHUNK_SIZE = 1024 * 64  # 64KB buffer for optimal I/O / 최적의 I/O를 위한 64KB 버퍼


def _parse_http_datetime(http_datetime: str) -> float | None:
    try:
        dt = email.utils.parsedate_to_datetime(http_datetime)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except Exception:
        return None


def _compute_retry_delay(
    attempt: int,
    *,
    retry_delay: float,
    retry_backoff: str,
    retry_max_delay: float,
) -> float:
    retry_delay = max(0.0, float(retry_delay))
    retry_max_delay = max(0.0, float(retry_max_delay))

    if retry_backoff == "fixed":
        wait_time = retry_delay
    elif retry_backoff == "linear":
        wait_time = retry_delay * attempt
    else:
        wait_time = retry_delay * (2 ** (attempt - 1))

    if retry_max_delay > 0:
        wait_time = min(wait_time, retry_max_delay)
    return wait_time


def get_file_info(
    url: str, headers: dict[str, str], session: Session | None = None
) -> tuple[int | None, bool, dict[str, str]]:
    """
    Retrieve file size and check if Range requests are supported using HEAD and GET probe.
    Also returns important headers for integrity and filename extraction.
    """
    size = None
    range_supported = False
    info_headers = {}

    owns_session = session is None
    session = session or Session()

    try:
        # Step 1: Try HEAD request
        try:
            response = session.head(url, headers=headers)
            if response.status_code == 200:
                size_raw = response.headers.get("Content-Length")
                if size_raw:
                    size = int(size_raw)
                if response.headers.get("Accept-Ranges") == "bytes":
                    range_supported = True

                # Capture headers for integrity and filename
                for h in ["ETag", "Last-Modified", "Content-Disposition"]:
                    val = response.headers.get(h)
                    if val:
                        info_headers[h] = val

        except Exception as e:
            logger.debug(f"HEAD request failed for {url}: {e}")

        # Step 2: Probe with small Range GET if not confirmed
        if not range_supported:
            try:
                probe_headers = headers.copy()
                probe_headers["Range"] = "bytes=0-0"
                response = session.get(url, headers=probe_headers)
                if response.status_code == 206:
                    range_supported = True
                    if size is None:
                        cr = response.headers.get("Content-Range")
                        if cr and "/" in cr:
                            size_raw = cr.split("/")[-1]
                            if size_raw.isdigit():
                                size = int(size_raw)

                    # Also capture headers from GET response if not present
                    for h in ["ETag", "Last-Modified", "Content-Disposition"]:
                        if h not in info_headers:
                            val = response.headers.get(h)
                            if val:
                                info_headers[h] = val
            except Exception as e:
                logger.debug(f"Range probe failed for {url}: {e}")
    finally:
        if owns_session:
            session.close()

    return size, range_supported, info_headers


def download_range(
    url: str,
    start: int,
    end: int,
    headers: dict[str, str],
    tmp_path: str,
    pbar: ProgressBar,
    limiter: SpeedLimiter | None,
    error_event: threading.Event,
    session: Session | None = None,
) -> None:
    """
    Download a specific byte range of a file. Used for multi-threaded downloads.
    """
    owns_session = session is None
    session = session or Session()
    req_headers = headers.copy()
    req_headers["Range"] = f"bytes={start}-{end}"

    try:
        response = session.get(url, headers=req_headers, stream=True)
        if response.status_code != 206:
            raise IntegrityError(
                f"Server at {url} does not support Range requests (Status: {response.status_code})."
            )

        with open(tmp_path, "r+b") as f:
            f.seek(start)
            for chunk in response.iter_bytes(CHUNK_SIZE):
                if error_event.is_set():
                    break
                if limiter:
                    limiter.wait(len(chunk))
                f.write(chunk)
                if pbar:
                    pbar.update(len(chunk))
    except Exception as e:
        logger.error(f"Range download failed for {url} ({start}-{end}): {e}")
        error_event.set()
        raise
    finally:
        if owns_session:
            session.close()


def download_file(
    url: str,
    output: str | None = None,
    resume: bool = False,
    threads: int = 1,
    max_speed: str | None = None,
    headers: dict[str, str] | None = None,
    progress_position: int = 0,
    ignore_cache: bool = False,
    mode: str = "fast",
    force: bool = False,
    skip_existing: bool = False,
    retries: int = 3,
    show_progress: bool = True,
    retry_delay: float = 1.0,
    retry_backoff: str = "exponential",
    retry_max_delay: float = 30.0,
    timestamping: bool = False,
) -> dict[str, object]:
    """
    Main orchestrator for downloading a single file with retries and integrity checks.
    Returns {"output", "bytes", "skipped"} for automation and JSON output.
    """
    base_headers = dict(headers or {})

    # Retry loop
    attempt = 0
    session = Session()
    try:
        while attempt <= retries:
            pbar = None
            try:
                request_headers = dict(base_headers)
                attempt_threads = threads
                output_was_provided = output is not None
                total_size = None
                range_supported = False
                info_headers: dict[str, str] = {}
                resolved_output = (
                    output if output_was_provided else get_filename_from_url(url)
                )

                should_probe = (
                    mode == "accurate" or attempt_threads > 1 or resume or timestamping
                )
                if should_probe:
                    # Get file info (size, range support, ETag, etc.)
                    total_size, range_supported, info_headers = get_file_info(
                        url, request_headers, session=session
                    )
                    if not output_was_provided:
                        resolved_output = get_filename_from_headers(info_headers, url)

                # 2. Auto-create directory
                out_dir = os.path.dirname(resolved_output)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)

                # Skip/overwrite policy must be handled before any network download.
                if not should_download_output(
                    resolved_output, force=force, skip_existing=skip_existing
                ):
                    return {
                        "output": resolved_output,
                        "bytes": 0,
                        "skipped": True,
                    }

                if timestamping and os.path.exists(resolved_output):
                    remote_modified_raw = info_headers.get("Last-Modified")
                    remote_modified = (
                        _parse_http_datetime(remote_modified_raw)
                        if remote_modified_raw
                        else None
                    )
                    if remote_modified is not None:
                        local_modified = os.path.getmtime(resolved_output)
                        if local_modified >= remote_modified:
                            logger.info(
                                f"Local file is up-to-date. Skipping: {resolved_output}"
                            )
                            return {
                                "output": resolved_output,
                                "bytes": 0,
                                "skipped": True,
                            }

                tmp_path = resolved_output + ".part"
                if ignore_cache and os.path.exists(tmp_path):
                    os.remove(tmp_path)

                # Validation for multi-threading
                if attempt_threads > 1 and not range_supported:
                    logger.debug(
                        f"Server does not support Range for {url}. Falling back to single-threaded."
                    )
                    attempt_threads = 1
                if attempt_threads > 1 and not total_size:
                    logger.debug(
                        f"Unknown content length for {url}. Falling back to single-threaded."
                    )
                    attempt_threads = 1

                downloaded_size = 0
                bytes_written = 0
                mode_flag = "wb"

                if resume and os.path.exists(tmp_path):
                    downloaded_size = os.path.getsize(tmp_path)
                    if total_size and downloaded_size >= total_size:
                        if safe_rename(tmp_path, resolved_output, force, skip_existing):
                            logger.info(f"File already complete: {resolved_output}")
                        return {
                            "output": resolved_output,
                            "bytes": downloaded_size,
                            "skipped": True,
                        }

                    if attempt_threads > 1:
                        logger.debug(
                            "Resuming multi-threaded download is not fully supported. Falling back to single-threaded."
                        )
                        attempt_threads = 1

                    request_headers["Range"] = f"bytes={downloaded_size}-"
                    mode_flag = "ab"

                parsed_speed = parse_speed(max_speed) if max_speed else None
                limiter = SpeedLimiter(parsed_speed) if parsed_speed else None

                pbar = (
                    ProgressBar(
                        total_size,
                        desc=os.path.basename(resolved_output)[:20],
                        position=progress_position,
                    )
                    if show_progress
                    else None
                )
                if pbar and downloaded_size > 0:
                    pbar.update(downloaded_size)

                if attempt_threads == 1:
                    response = session.get(url, headers=request_headers, stream=True)
                    if resume and downloaded_size > 0 and response.status_code != 206:
                        raise IntegrityError(
                            f"Server does not support resume for {url} (Status: {response.status_code})."
                        )
                    if not (resume and downloaded_size > 0):
                        response.raise_for_status()

                    with open(tmp_path, mode_flag) as f:
                        for chunk in response.iter_bytes(CHUNK_SIZE):
                            if limiter:
                                limiter.wait(len(chunk))
                            f.write(chunk)
                            bytes_written += len(chunk)
                            if pbar:
                                pbar.update(len(chunk))
                else:
                    if not os.path.exists(tmp_path):
                        with open(tmp_path, "wb") as f:
                            if total_size:
                                f.truncate(total_size)

                    error_event = threading.Event()
                    range_list = []
                    part_size = total_size // attempt_threads
                    for i in range(attempt_threads):
                        start = i * part_size
                        end = (
                            total_size - 1
                            if i == attempt_threads - 1
                            else (start + part_size - 1)
                        )
                        range_list.append((start, end))

                    # ThreadPoolExecutor propagates the original worker exception
                    # through future.result(); error_event makes peers abort early.
                    with ThreadPoolExecutor(max_workers=attempt_threads) as pool:
                        futures = [
                            pool.submit(
                                download_range,
                                url,
                                start,
                                end,
                                request_headers,
                                tmp_path,
                                pbar,
                                limiter,
                                error_event,
                                session,
                            )
                            for start, end in range_list
                        ]
                        for future in futures:
                            future.result()

                if pbar:
                    pbar.close()
                if not safe_rename(tmp_path, resolved_output, force, skip_existing):
                    if not os.path.exists(tmp_path):
                        return {
                            "output": resolved_output,
                            "bytes": 0,
                            "skipped": True,
                        }
                    raise DownloadError(f"Failed to save {resolved_output}")

                logger.info(f"Successfully downloaded: {resolved_output}")
                final_bytes = (
                    downloaded_size + bytes_written
                    if attempt_threads == 1
                    else (total_size or 0)
                )
                return {
                    "output": resolved_output,
                    "bytes": final_bytes,
                    "skipped": False,
                }

            except (urllib.error.URLError, TimeoutError, EasyGetError) as e:
                if isinstance(e, EasyGetError) and not e.retryable:
                    if pbar:
                        pbar.close()
                    raise
                attempt += 1
                if attempt > retries:
                    if pbar:
                        pbar.close()
                    raise DownloadError(
                        f"Download failed after {retries} retries: {e}"
                    ) from e

                wait_time = _compute_retry_delay(
                    attempt,
                    retry_delay=retry_delay,
                    retry_backoff=retry_backoff,
                    retry_max_delay=retry_max_delay,
                )
                logger.warning(
                    f"\nDownload failed: {e}. Retrying in {wait_time}s... ({attempt}/{retries})"
                )
                time.sleep(wait_time)
            except Exception as e:
                if pbar:
                    pbar.close()
                raise DownloadError(f"Unrecoverable error: {e}") from e
    finally:
        session.close()
