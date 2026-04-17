import os
import threading
import logging
import email.utils
from datetime import timezone
import urllib.request
import urllib.error
from typing import Optional, Dict, Tuple
from .exceptions import EasyGetError, DownloadError, IntegrityError
from .utils import (
    ProgressBar,
    SpeedLimiter,
    safe_rename,
    parse_speed,
    get_filename_from_headers,
    get_filename_from_url,
    should_download_output,
)

from .session import Session
from .models import Response

# Constants / 상수
CHUNK_SIZE = 1024 * 64  # 64KB buffer for optimal I/O / 최적의 I/O를 위한 64KB 버퍼

def _parse_http_datetime(http_datetime: str) -> Optional[float]:
    try:
        dt = email.utils.parsedate_to_datetime(http_datetime)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
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

def get_file_info(url: str, headers: Dict[str, str], session: Optional[Session] = None) -> Tuple[Optional[int], bool, Dict[str, str]]:
    """
    Retrieve file size and check if Range requests are supported using HEAD and GET probe.
    Also returns important headers for integrity and filename extraction.
    """
    size = None
    range_supported = False
    info_headers = {}
    
    session = session or Session()
    
    try:
        # Step 1: Try HEAD request
        response = session.head(url, headers=headers)
        if response.status_code == 200:
            size_raw = response.headers.get('Content-Length')
            if size_raw: size = int(size_raw)
            if response.headers.get('Accept-Ranges') == 'bytes':
                range_supported = True
            
            # Capture headers for integrity and filename
            for h in ['ETag', 'Last-Modified', 'Content-Disposition']:
                val = response.headers.get(h)
                if val: info_headers[h] = val
                
    except Exception as e:
        logging.debug(f"HEAD request failed for {url}: {e}")

    # Step 2: Probe with small Range GET if not confirmed
    if not range_supported:
        try:
            probe_headers = headers.copy()
            probe_headers['Range'] = 'bytes=0-0'
            response = session.get(url, headers=probe_headers)
            if response.status_code == 206:
                range_supported = True
                if size is None:
                    cr = response.headers.get('Content-Range')
                    if cr and '/' in cr:
                        size_raw = cr.split('/')[-1]
                        if size_raw.isdigit(): size = int(size_raw)
                
                # Also capture headers from GET response if not present
                for h in ['ETag', 'Last-Modified', 'Content-Disposition']:
                    if h not in info_headers:
                        val = response.headers.get(h)
                        if val: info_headers[h] = val
        except Exception as e:
            logging.debug(f"Range probe failed for {url}: {e}")

    return size, range_supported, info_headers

def download_range(url: str, start: int, end: int, headers: Dict[str, str],
                   tmp_path: str, pbar: ProgressBar, limiter: Optional[SpeedLimiter],
                   error_event: threading.Event, session: Optional[Session] = None) -> None:
    """
    Download a specific byte range of a file. Used for multi-threaded downloads.
    """
    session = session or Session()
    req_headers = headers.copy()
    req_headers['Range'] = f'bytes={start}-{end}'
    
    try:
        response = session.get(url, headers=req_headers, stream=True)
        if response.status_code != 206:
            raise IntegrityError(f"Server at {url} does not support Range requests (Status: {response.status_code}).")

        with open(tmp_path, 'r+b') as f:
            f.seek(start)
            for chunk in response.iter_bytes(CHUNK_SIZE):
                if error_event.is_set(): break
                if limiter: limiter.wait(len(chunk))
                f.write(chunk)
                if pbar: pbar.update(len(chunk))
    except Exception as e:
        logging.error(f"Range download failed for {url} ({start}-{end}): {e}")
        error_event.set()

def download_file(url: str, output: Optional[str] = None, resume: bool = False, threads: int = 1,
                  max_speed: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
                  progress_position: int = 0, ignore_cache: bool = False, mode: str = "fast",
                  force: bool = False, skip_existing: bool = False, retries: int = 3,
                  show_progress: bool = True, retry_delay: float = 1.0,
                  retry_backoff: str = "exponential", retry_max_delay: float = 30.0,
                  timestamping: bool = False) -> None:
    """
    Main orchestrator for downloading a single file with retries and integrity checks.
    """
    import time
    base_headers = dict(headers or {})
    
    # Retry loop
    attempt = 0
    session = Session()
    while attempt <= retries:
        try:
            request_headers = dict(base_headers)
            attempt_threads = threads
            output_was_provided = output is not None
            total_size = None
            range_supported = False
            info_headers: Dict[str, str] = {}
            resolved_output = output if output_was_provided else get_filename_from_url(url)

            should_probe = mode == "accurate" or attempt_threads > 1 or resume or timestamping
            if should_probe:
                # Get file info (size, range support, ETag, etc.)
                total_size, range_supported, info_headers = get_file_info(url, request_headers, session=session)
                if not output_was_provided:
                    resolved_output = get_filename_from_headers(info_headers, url)
            
            # 2. Auto-create directory
            out_dir = os.path.dirname(resolved_output)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)

            # Skip/overwrite policy must be handled before any network download.
            if not should_download_output(resolved_output, force=force, skip_existing=skip_existing):
                return

            if timestamping and os.path.exists(resolved_output):
                remote_modified_raw = info_headers.get("Last-Modified")
                remote_modified = _parse_http_datetime(remote_modified_raw) if remote_modified_raw else None
                if remote_modified is not None:
                    local_modified = os.path.getmtime(resolved_output)
                    if local_modified >= remote_modified:
                        logging.info(f"Local file is up-to-date. Skipping: {resolved_output}")
                        return
                
            tmp_path = resolved_output + ".part"
            if ignore_cache and os.path.exists(tmp_path):
                os.remove(tmp_path)

            # Validation for multi-threading
            if attempt_threads > 1 and not range_supported:
                logging.debug(f"Server does not support Range for {url}. Falling back to single-threaded.")
                attempt_threads = 1
            if attempt_threads > 1 and not total_size:
                logging.debug(f"Unknown content length for {url}. Falling back to single-threaded.")
                attempt_threads = 1

            downloaded_size = 0
            mode_flag = 'wb'
            
            if resume and os.path.exists(tmp_path):
                downloaded_size = os.path.getsize(tmp_path)
                if total_size and downloaded_size >= total_size:
                    if safe_rename(tmp_path, resolved_output, force, skip_existing):
                        logging.info(f"File already complete: {resolved_output}")
                    return
                
                if attempt_threads > 1:
                    logging.debug("Resuming multi-threaded download is not fully supported. Falling back to single-threaded.")
                    attempt_threads = 1
                    
                request_headers['Range'] = f'bytes={downloaded_size}-'
                mode_flag = 'ab'

            parsed_speed = parse_speed(max_speed) if max_speed else None
            limiter = SpeedLimiter(parsed_speed) if parsed_speed else None

            pbar = ProgressBar(total_size, desc=os.path.basename(resolved_output)[:20], position=progress_position) if show_progress else None
            if pbar and downloaded_size > 0: pbar.update(downloaded_size)

            if attempt_threads == 1:
                response = session.get(url, headers=request_headers, stream=True)
                if resume and downloaded_size > 0 and response.status_code != 206:
                    raise IntegrityError(f"Server does not support resume for {url} (Status: {response.status_code}).")
                if not (resume and downloaded_size > 0):
                    response.raise_for_status()
                
                with open(tmp_path, mode_flag) as f:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        if limiter: limiter.wait(len(chunk))
                        f.write(chunk)
                        if pbar: pbar.update(len(chunk))
            else:
                if not os.path.exists(tmp_path):
                    with open(tmp_path, 'wb') as f:
                        if total_size: f.truncate(total_size)
                
                error_event = threading.Event()
                range_list = []
                part_size = total_size // attempt_threads
                for i in range(attempt_threads):
                    start = i * part_size
                    end = total_size - 1 if i == attempt_threads - 1 else (start + part_size - 1)
                    range_list.append((start, end))
                
                thread_pool = []
                for start, end in range_list:
                    t = threading.Thread(target=download_range, args=(url, start, end, request_headers, tmp_path, pbar, limiter, error_event, session))
                    thread_pool.append(t)
                    t.start()
                
                for t in thread_pool: t.join()
                if error_event.is_set():
                    raise DownloadError(f"One or more threads failed for {url}")

            if pbar: pbar.close()
            if not safe_rename(tmp_path, resolved_output, force, skip_existing):
                if not os.path.exists(tmp_path):
                    return
                raise DownloadError(f"Failed to save {resolved_output}")
            
            logging.info(f"Successfully downloaded: {resolved_output}")
            return

        except (urllib.error.URLError, DownloadError, IntegrityError, TimeoutError) as e:
            attempt += 1
            if attempt > retries:
                if 'pbar' in locals() and pbar: pbar.close()
                raise DownloadError(f"Download failed after {retries} retries: {e}") from e
            
            wait_time = _compute_retry_delay(
                attempt,
                retry_delay=retry_delay,
                retry_backoff=retry_backoff,
                retry_max_delay=retry_max_delay,
            )
            logging.warning(f"\nDownload failed: {e}. Retrying in {wait_time}s... ({attempt}/{retries})")
            time.sleep(wait_time)
        except Exception as e:
            if 'pbar' in locals() and pbar: pbar.close()
            raise DownloadError(f"Unrecoverable error: {e}") from e
