import email.utils
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC

from .exceptions import DownloadError, EasyGetError, IntegrityError
from .session import Session, TimeoutType
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
CHUNK_SIZE = 1024 * 256  # 256KB buffer for optimal I/O / 최적의 I/O를 위한 256KB 버퍼


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


def _segment_meta_path(tmp_path: str) -> str:
    return tmp_path + ".meta"


def _new_segment_meta(
    total_size: int, threads: int, info_headers: dict[str, str]
) -> dict[str, object]:
    """Build the sidecar describing which byte ranges still need fetching."""
    part_size = total_size // threads
    ranges = []
    for i in range(threads):
        start = i * part_size
        end = total_size - 1 if i == threads - 1 else start + part_size - 1
        ranges.append({"start": start, "end": end, "done": False})
    return {
        "version": 1,
        "size": total_size,
        "etag": info_headers.get("ETag"),
        "last_modified": info_headers.get("Last-Modified"),
        "ranges": ranges,
    }


def _write_segment_meta(path: str, meta: dict[str, object]) -> None:
    """Persist segment state atomically so a crash can't leave a torn file."""
    staging = path + ".tmp"
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(staging, path)


def _load_segment_meta(
    path: str, total_size: int | None, info_headers: dict[str, str]
) -> dict[str, object] | None:
    """
    Load segment state if it provably matches the remote file, else None.
    A .part file preallocated for range writes is full of zero holes that are
    indistinguishable from real bytes by size alone, so an untrusted meta is
    worse than none at all.
    """
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(meta, dict) or meta.get("size") != total_size:
        return None
    for key, header in (("etag", "ETag"), ("last_modified", "Last-Modified")):
        stored, current = meta.get(key), info_headers.get(header)
        if stored and current and stored != current:
            return None

    ranges = meta.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        return None
    covered = 0
    for seg in ranges:
        if not isinstance(seg, dict) or not isinstance(seg.get("done"), bool):
            return None
        start, end = seg.get("start"), seg.get("end")
        if not (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end < total_size
        ):
            return None
        covered += end - start + 1
    return meta if covered == total_size else None


def get_file_info(
    url: str,
    headers: dict[str, str],
    session: Session | None = None,
    timeout: TimeoutType = 30,
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
            response = session.head(url, headers=headers, timeout=timeout)
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
                # stream=True: a server that ignores Range would otherwise
                # buffer the entire body in memory just for this probe.
                response = session.get(
                    url, headers=probe_headers, stream=True, timeout=timeout
                )
                try:
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
                finally:
                    response.close()
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
    timeout: TimeoutType = 30,
) -> None:
    """
    Download a specific byte range of a file. Used for multi-threaded downloads.
    """
    owns_session = session is None
    session = session or Session()
    req_headers = headers.copy()
    req_headers["Range"] = f"bytes={start}-{end}"

    try:
        response = session.get(url, headers=req_headers, stream=True, timeout=timeout)
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
    timeout: TimeoutType = 30,
) -> dict[str, object]:
    """
    Main orchestrator for downloading a single file with retries and integrity checks.
    Returns {"output", "bytes", "skipped"} for automation and JSON output.
    """
    base_headers = dict(headers or {})

    # Retry loop
    attempt = 0
    meta_created = False  # segment meta written by this call, reusable on retry
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
                        url, request_headers, session=session, timeout=timeout
                    )
                    if not output_was_provided:
                        resolved_output = get_filename_from_headers(info_headers, url)

                # 2. Auto-create directory
                out_dir = os.path.dirname(resolved_output)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)

                # Timestamping runs before the overwrite prompt: wget -N
                # semantics skip up-to-date files without asking anything.
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

                # Skip/overwrite policy must be handled before any network download.
                if not should_download_output(
                    resolved_output, force=force, skip_existing=skip_existing
                ):
                    return {
                        "output": resolved_output,
                        "bytes": 0,
                        "skipped": True,
                    }

                tmp_path = resolved_output + ".part"
                meta_path = _segment_meta_path(tmp_path)
                if ignore_cache:
                    for stale in (tmp_path, meta_path):
                        if os.path.exists(stale):
                            os.remove(stale)
                    meta_created = False

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

                # Segmented .part files are preallocated to full size up front,
                # so byte-count alone cannot tell written ranges from zero
                # holes — only the .meta sidecar can. Anything unverifiable is
                # restarted rather than risk renaming a hollow file "complete".
                segment_meta = None
                if os.path.exists(meta_path):
                    if attempt_threads > 1 and (resume or meta_created):
                        segment_meta = _load_segment_meta(
                            meta_path, total_size, info_headers
                        )
                    tmp_usable = (
                        os.path.exists(tmp_path)
                        and os.path.getsize(tmp_path) == total_size
                    )
                    if segment_meta is None or not tmp_usable:
                        segment_meta = None
                        meta_created = False
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                        os.remove(meta_path)

                downloaded_size = 0
                bytes_written = 0
                mode_flag = "wb"

                # Sequential .part files grow only by appending, so a full-size
                # one without a meta file is genuinely complete.
                if resume and segment_meta is None and os.path.exists(tmp_path):
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
                            "Sequential .part files resume by appending; falling back to single-threaded."
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
                    response = session.get(
                        url, headers=request_headers, stream=True, timeout=timeout
                    )
                    if 300 <= response.status_code < 400:
                        raise IntegrityError(
                            f"Unexpected redirect response {response.status_code} for {url}."
                        )
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
                    if segment_meta is None:
                        segment_meta = _new_segment_meta(
                            total_size, attempt_threads, info_headers
                        )
                        _write_segment_meta(meta_path, segment_meta)
                        meta_created = True
                        with open(tmp_path, "wb") as f:
                            f.truncate(total_size)

                    if pbar:
                        done_bytes = sum(
                            seg["end"] - seg["start"] + 1
                            for seg in segment_meta["ranges"]
                            if seg["done"]
                        )
                        if done_bytes:
                            pbar.update(done_bytes)

                    pending = [
                        i
                        for i, seg in enumerate(segment_meta["ranges"])
                        if not seg["done"]
                    ]
                    if pending:
                        error_event = threading.Event()
                        # ThreadPoolExecutor propagates the worker exception via
                        # future.result(); error_event makes peers abort early.
                        # Completed ranges are marked in meta as they finish so
                        # a later resume only refetches what is still missing.
                        with ThreadPoolExecutor(
                            max_workers=min(attempt_threads, len(pending))
                        ) as pool:
                            futures = {}
                            for i in pending:
                                seg = segment_meta["ranges"][i]
                                future = pool.submit(
                                    download_range,
                                    url,
                                    seg["start"],
                                    seg["end"],
                                    request_headers,
                                    tmp_path,
                                    pbar,
                                    limiter,
                                    error_event,
                                    session,
                                    timeout,
                                )
                                futures[future] = i
                            for future in as_completed(futures):
                                future.result()
                                segment_meta["ranges"][futures[future]]["done"] = True
                                _write_segment_meta(meta_path, segment_meta)

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
                if os.path.exists(meta_path):
                    os.remove(meta_path)

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
                        f"Download failed after {attempt} attempts ({retries} retries): {e}"
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
