import os
import sys
import time
import logging
from typing import Optional, Dict
from urllib.parse import urlparse

import re
import threading
from urllib.parse import urlparse, unquote

# Global state for overwrite/skip behavior / 덮어쓰기 및 건너뛰기 동작을 위한 전역 상태
_OVERWRITE_ALL: bool = False
_SKIP_ALL: bool = False
_GLOBAL_LOCK = threading.Lock()

def set_overwrite_all(value: bool) -> None:
    """Set the global flag to overwrite all existing files. / 모든 기존 파일을 덮어쓰도록 전역 플래그를 설정합니다."""
    with _GLOBAL_LOCK:
        global _OVERWRITE_ALL
        _OVERWRITE_ALL = value

def set_skip_all(value: bool) -> None:
    """Set the global flag to skip all existing files. / 모든 기존 파일을 건너뛰도록 전역 플래그를 설정합니다."""
    with _GLOBAL_LOCK:
        global _SKIP_ALL
        _SKIP_ALL = value

class ProgressBar:
    """
    A lightweight, zero-dependency progress bar for terminal output.
    """
    def __init__(self, total: Optional[int], desc: str = "", position: int = 0, unit: str = "B"):
        self.total: Optional[int] = total
        self.desc: str = desc
        self.position: int = position
        self.unit: str = unit
        self.current: int = 0
        self.start_time: float = time.time()
        self._last_update: float = 0.0
        self._lock = threading.Lock()
        self._spinner = ["|", "/", "-", "\\"]
        self._spinner_idx = 0

    def update(self, n: int) -> None:
        """Increment progress and refresh display."""
        with self._lock:
            self.current += n
            now = time.time()
            if now - self._last_update > 0.1 or (self.total and self.current >= self.total):
                self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner)
                self.display()
                self._last_update = now

    def display(self) -> None:
        """Render the bar to stdout using ANSI escape codes."""
        if not sys.stdout.isatty():
            return

        elapsed = time.time() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0
        
        # Calculate percentage and bar
        if self.total:
            percent = (self.current / self.total * 100)
            bar_len = 25
            filled_len = int(bar_len * percent / 100)
            bar = "=" * filled_len + "-" * (bar_len - filled_len)
            pct_str = f"{percent:5.1f}%"
        else:
            # Spinner for unknown size
            bar_len = 25
            idx = self._spinner_idx % len(self._spinner)
            bar = (" " * (self._spinner_idx % bar_len)) + self._spinner[idx] + (" " * (bar_len - (self._spinner_idx % bar_len) - 1))
            pct_str = "  N/A%"
        
        # Position the cursor for multi-bar support
        prefix = f"\033[{self.position}B\r" if self.position > 0 else "\r"
        suffix = f"\033[{self.position}A" if self.position > 0 else ""
        
        # Format speed and size
        if self.unit == "B":
            if speed > 1024 * 1024:
                speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
            else:
                speed_str = f"{speed / 1024:.2f} KB/s"
                
            total_str = f"{self.total / 1024 / 1024:.1f}MB" if self.total else "?"
            curr_str = f"{self.current / 1024 / 1024:.1f}MB"
        else:
            speed_str = f"{speed:.2f} {self.unit}/s"
            total_str = str(self.total) if self.total else "?"
            curr_str = str(self.current)
        
        output = f"{prefix}{self.desc:20}: [{bar}] {pct_str} | {curr_str}/{total_str} | {speed_str}{suffix}"
        sys.stdout.write(output)
        sys.stdout.flush()

    def close(self) -> None:
        """Ensure the terminal cursor is moved past the progress bar area."""
        with self._lock:
            if sys.stdout.isatty():
                if self.position > 0:
                    sys.stdout.write(f"\033[{self.position}B\n")
                else:
                    sys.stdout.write("\n")
                sys.stdout.flush()

class SpeedLimiter:
    """
    Limits the byte-per-second rate of data transfer.
    데이터 전송의 초당 바이트 속도를 제한합니다.
    """
    def __init__(self, max_speed: int):
        self.max_speed: int = max_speed
        self.start_time: float = time.time()
        self.downloaded: int = 0
        self._lock = threading.Lock()

    def wait(self, chunk_size: int) -> None:
        """Wait if current throughput exceeds max_speed. / 현재 처리량이 최대 속도를 초과하면 대기합니다."""
        with self._lock:
            self.downloaded += chunk_size
            elapsed = time.time() - self.start_time
            expected = self.downloaded / self.max_speed
            
            if expected > elapsed:
                time.sleep(expected - elapsed)

def parse_speed(speed_str: str) -> Optional[int]:
    """Parse speed string (1M, 500K) into bytes per second. / 속도 문자열(1M, 500K)을 초당 바이트로 변환합니다."""
    try:
        speed_str = speed_str.strip().upper()
        if speed_str.endswith("M"):
            speed = float(speed_str[:-1]) * 1024 * 1024
        elif speed_str.endswith("K"):
            speed = float(speed_str[:-1]) * 1024
        else:
            speed = float(speed_str)
        speed_int = int(speed)
        return speed_int if speed_int >= 1 else None
    except ValueError:
        return None

def get_filename_from_headers(headers: Dict[str, str], url: str) -> str:
    """Extract filename from Content-Disposition header or fallback to URL."""
    cd = headers.get('Content-Disposition')
    if cd:
        # Regex to find filename from header
        fname = re.findall(r'filename=["\']?([^"\']+)["\']?', cd)
        if fname: return unquote(fname[0])
    
    return get_filename_from_url(url)

def get_filename_from_url(url: str) -> str:
    """Extract the filename from a URL path. / URL 경로에서 파일명을 추출합니다."""
    path = urlparse(url).path
    return os.path.basename(path) or "downloaded.file"

def safe_rename(tmp_path: str, output: str, force: bool = False, skip_existing: bool = False) -> bool:
    """
    Safely moves the temporary file to the final destination.
    임시 파일을 최종 목적지로 안전하게 이동합니다.
    """
    global _OVERWRITE_ALL, _SKIP_ALL
    
    if os.path.exists(output):
        if force or _OVERWRITE_ALL:
            pass
        elif skip_existing or _SKIP_ALL:
            logging.info(f"File '{output}' already exists. Skipping.")
            if os.path.exists(tmp_path): os.remove(tmp_path)
            return False
        else:
            if not sys.stdin.isatty():
                logging.warning(f"File '{output}' exists in non-interactive mode. Skipping.")
                if os.path.exists(tmp_path): os.remove(tmp_path)
                return False
            
            # Interactive prompt / 사용자 대화형 확인
            prompt = f"File '{output}' exists. Overwrite? [y/n/a(ll)/i(skip all)]: "
            ans = input(prompt).lower().strip()
            if ans == 'a': _OVERWRITE_ALL = True
            elif ans == 'i': 
                _SKIP_ALL = True
                if os.path.exists(tmp_path): os.remove(tmp_path)
                return False
            elif ans != 'y':
                if os.path.exists(tmp_path): os.remove(tmp_path)
                return False
                
    try:
        os.replace(tmp_path, output)
        return True
    except Exception as e:
        logging.error(f"Failed to save file '{output}': {e}")
        return False
