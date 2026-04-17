import os
import threading
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Tuple
from .exceptions import EasyGetError, DownloadError, IntegrityError
from .utils import ProgressBar, SpeedLimiter, safe_rename, parse_speed, get_filename_from_headers

# Constants / 상수
CHUNK_SIZE = 1024 * 64  # 64KB buffer for optimal I/O / 최적의 I/O를 위한 64KB 버퍼

def get_file_info(url: str, headers: Dict[str, str]) -> Tuple[Optional[int], bool, Dict[str, str]]:
    """
    Retrieve file size and check if Range requests are supported using HEAD and GET probe.
    Also returns important headers for integrity and filename extraction.
    """
    size = None
    range_supported = False
    info_headers = {}
    
    try:
        # Step 1: Try HEAD request
        req = urllib.request.Request(url, headers=headers, method='HEAD')
        with urllib.request.urlopen(req, timeout=10) as response:
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
            req = urllib.request.Request(url, headers=probe_headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 206:
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
                   error_event: threading.Event) -> None:
    """
    Download a specific byte range of a file. Used for multi-threaded downloads.
    파일의 특정 바이트 범위를 다운로드합니다. 멀티스레드 다운로드에 사용됩니다.
    """
    req_headers = headers.copy()
    req_headers['Range'] = f'bytes={start}-{end}'
    req = urllib.request.Request(url, headers=req_headers)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status != 206:
                raise IntegrityError(f"Server at {url} does not support Range requests (Status: {response.status}).")

            with open(tmp_path, 'r+b') as f:
                f.seek(start)
                while not error_event.is_set():
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk: break
                    if limiter: limiter.wait(len(chunk))
                    f.write(chunk)
                    pbar.update(len(chunk))
    except Exception as e:
        logging.error(f"Range download failed for {url} ({start}-{end}): {e}")
        error_event.set()

def download_file(url: str, output: Optional[str] = None, resume: bool = False, threads: int = 1,
                  max_speed: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
                  progress_position: int = 0, ignore_cache: bool = False, mode: str = "fast",
                  force: bool = False, skip_existing: bool = False, retries: int = 3) -> None:
    """
    Main orchestrator for downloading a single file with retries and integrity checks.
    """
    import time
    headers = dict(headers or {})
    
    # Retry loop
    attempt = 0
    while attempt <= retries:
        try:
            # Get file info (size, range support, ETag, etc.)
            total_size, range_supported, info_headers = get_file_info(url, headers)
            
            # 1. Determine final output filename if not provided
            if not output:
                output = get_filename_from_headers(info_headers, url)
            
            # 2. Auto-create directory
            out_dir = os.path.dirname(output)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)
                
            tmp_path = output + ".part"
            if ignore_cache and os.path.exists(tmp_path):
                os.remove(tmp_path)

            # Validation for multi-threading
            if threads > 1 and not range_supported:
                logging.debug(f"Server does not support Range for {url}. Falling back to single-threaded.")
                threads = 1

            downloaded_size = 0
            mode_flag = 'wb'
            
            if resume and os.path.exists(tmp_path):
                downloaded_size = os.path.getsize(tmp_path)
                if total_size and downloaded_size >= total_size:
                    if safe_rename(tmp_path, output, force, skip_existing):
                        logging.info(f"File already complete: {output}")
                    return
                
                if threads > 1:
                    logging.debug("Resuming multi-threaded download is not fully supported. Falling back to single-threaded.")
                    threads = 1
                    
                headers['Range'] = f'bytes={downloaded_size}-'
                mode_flag = 'ab'

            parsed_speed = parse_speed(max_speed) if max_speed else None
            limiter = SpeedLimiter(parsed_speed) if parsed_speed else None

            pbar = ProgressBar(total_size, desc=os.path.basename(output)[:20], position=progress_position)
            if downloaded_size > 0: pbar.update(downloaded_size)

            if threads == 1:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as response:
                    if resume and downloaded_size > 0 and response.status != 206:
                        raise IntegrityError(f"Server does not support resume for {url} (Status: {response.status}).")
                    
                    with open(tmp_path, mode_flag) as f:
                        while True:
                            chunk = response.read(CHUNK_SIZE)
                            if not chunk: break
                            if limiter: limiter.wait(len(chunk))
                            f.write(chunk)
                            pbar.update(len(chunk))
            else:
                if not os.path.exists(tmp_path):
                    with open(tmp_path, 'wb') as f:
                        if total_size: f.truncate(total_size)
                
                error_event = threading.Event()
                range_list = []
                part_size = total_size // threads
                for i in range(threads):
                    start = i * part_size
                    end = total_size - 1 if i == threads - 1 else (start + part_size - 1)
                    range_list.append((start, end))
                
                thread_pool = []
                for start, end in range_list:
                    t = threading.Thread(target=download_range, args=(url, start, end, headers, tmp_path, pbar, limiter, error_event))
                    thread_pool.append(t)
                    t.start()
                
                for t in thread_pool: t.join()
                if error_event.is_set():
                    raise DownloadError(f"One or more threads failed for {url}")

            pbar.close()
            if not safe_rename(tmp_path, output, force, skip_existing):
                raise DownloadError(f"Failed to save {output}")
            
            logging.info(f"Successfully downloaded: {output}")
            return

        except (urllib.error.URLError, DownloadError, IntegrityError, TimeoutError) as e:
            attempt += 1
            if attempt > retries:
                if 'pbar' in locals(): pbar.close()
                raise DownloadError(f"Download failed after {retries} retries: {e}") from e
            
            wait_time = 2 ** attempt
            logging.warning(f"\nDownload failed: {e}. Retrying in {wait_time}s... ({attempt}/{retries})")
            time.sleep(wait_time)
        except Exception as e:
            if 'pbar' in locals(): pbar.close()
            raise DownloadError(f"Unrecoverable error: {e}") from e
