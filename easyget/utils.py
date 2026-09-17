import logging
import os
import posixpath
import re
import sys
import threading
import time
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Global state for overwrite/skip behavior / 덮어쓰기 및 건너뛰기 동작을 위한 전역 상태
_OVERWRITE_ALL: bool = False
_SKIP_ALL: bool = False
_CONFIRMED_OVERWRITES: set[str] = set()
_GLOBAL_LOCK = threading.Lock()


def set_overwrite_all(value: bool) -> None:
    """Set the global flag to overwrite all existing files. / 모든 기존 파일을 덮어쓰도록 전역 플래그를 설정합니다."""
    global _OVERWRITE_ALL  # noqa: PLW0603 - shared CLI overwrite policy
    with _GLOBAL_LOCK:
        _OVERWRITE_ALL = value


def set_skip_all(value: bool) -> None:
    """Set the global flag to skip all existing files. / 모든 기존 파일을 건너뛰도록 전역 플래그를 설정합니다."""
    global _SKIP_ALL  # noqa: PLW0603 - shared CLI skip policy
    with _GLOBAL_LOCK:
        _SKIP_ALL = value


class ProgressBar:
    """
    A lightweight, zero-dependency progress bar for terminal output.
    """

    def __init__(
        self, total: int | None, desc: str = "", position: int = 0, unit: str = "B"
    ):
        self.total: int | None = total
        self.desc: str = desc
        self.position: int = position
        self.unit: str = unit
        self.current: int = 0
        self.start_time: float = time.monotonic()
        self._last_update: float = 0.0
        self._lock = threading.Lock()
        self._spinner = ["|", "/", "-", "\\"]
        self._spinner_idx = 0

    def update(self, n: int) -> None:
        """Increment progress and refresh display."""
        with self._lock:
            self.current += n
            now = time.monotonic()
            if now - self._last_update > 0.1 or (
                self.total and self.current >= self.total
            ):
                self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner)
                self.display()
                self._last_update = now

    def display(self) -> None:
        """Render the bar to stderr so stdout stays clean for pipes and JSON."""
        if not sys.stderr.isatty():
            return

        elapsed = time.monotonic() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0

        # Calculate percentage and bar
        if self.total:
            percent = self.current / self.total * 100
            bar_len = 25
            filled_len = int(bar_len * percent / 100)
            bar = "=" * filled_len + "-" * (bar_len - filled_len)
            pct_str = f"{percent:5.1f}%"
        else:
            # Spinner for unknown size
            bar_len = 25
            idx = self._spinner_idx % len(self._spinner)
            bar = (
                (" " * (self._spinner_idx % bar_len))
                + self._spinner[idx]
                + (" " * (bar_len - (self._spinner_idx % bar_len) - 1))
            )
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
        sys.stderr.write(output)
        sys.stderr.flush()

    def close(self) -> None:
        """Ensure the terminal cursor is moved past the progress bar area."""
        with self._lock:
            if sys.stderr.isatty():
                if self.position > 0:
                    sys.stderr.write(f"\033[{self.position}B\n")
                else:
                    sys.stderr.write("\n")
                sys.stderr.flush()


class SpeedLimiter:
    """
    Limits the byte-per-second rate of data transfer.
    데이터 전송의 초당 바이트 속도를 제한합니다.
    """

    def __init__(self, max_speed: int):
        self.max_speed: int = max_speed
        self.start_time: float = time.monotonic()
        self.downloaded: int = 0
        self._lock = threading.Lock()

    def wait(self, chunk_size: int) -> None:
        """Wait if current throughput exceeds max_speed. / 현재 처리량이 최대 속도를 초과하면 대기합니다."""
        with self._lock:
            self.downloaded += chunk_size
            elapsed = time.monotonic() - self.start_time
            expected = self.downloaded / self.max_speed
            delay = expected - elapsed

        if delay > 0:
            time.sleep(delay)


def parse_speed(speed_str: str) -> int | None:
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


# Device names that are invalid as filenames on Windows (checked against the stem).
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _sanitize_filename(name: str) -> str | None:
    """
    Reduce a server-supplied filename to a safe basename.
    서버가 보낸 파일명을 안전한 basename으로 정제합니다.
    """
    name = posixpath.basename(name.replace("\\", "/")).strip()
    stem = name.split(".", 1)[0].upper()
    if (
        not name
        or name in (".", "..")
        or stem in _WINDOWS_RESERVED_NAMES
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        return None
    return name


def get_filename_from_headers(headers: dict[str, str], url: str) -> str:
    """Extract filename from Content-Disposition header or fallback to URL."""
    cd = headers.get("Content-Disposition")
    if cd:
        # RFC 5987 filename*= takes precedence over the ASCII filename= form.
        star = re.search(r"filename\*\s*=\s*([^;\s]+)", cd, re.IGNORECASE)
        if star:
            raw = star.group(1).strip("\"'")
            _charset, _, encoded = raw.partition("''")
            name = _sanitize_filename(unquote(encoded or raw))
            if name:
                return name

        match = re.search(
            r'filename\s*=\s*"([^"]+)"'
            r"|filename\s*=\s*'([^']+)'"
            r"|filename\s*=\s*([^;\s]+)",
            cd,
            re.IGNORECASE,
        )
        if match:
            candidate = next(g for g in match.groups() if g is not None)
            name = _sanitize_filename(unquote(candidate))
            if name:
                return name

    return get_filename_from_url(url)


def get_filename_from_url(url: str) -> str:
    """Extract the filename from a URL path. / URL 경로에서 파일명을 추출합니다."""
    path = urlparse(url).path
    name = posixpath.basename(path)
    # wget-compatible fallback: directory or nameless URLs save as index.html
    if not name or path.endswith("/"):
        return "index.html"
    return name


def should_download_output(
    output: str, force: bool = False, skip_existing: bool = False
) -> bool:
    """
    Decide whether a download should proceed when the destination file already exists.
    기존 파일이 있을 때 다운로드를 계속할지 결정합니다.
    """
    global _OVERWRITE_ALL, _SKIP_ALL  # noqa: PLW0603 - 'a'/'i' answers update the shared policy

    if not os.path.exists(output):
        return True

    output_key = os.path.abspath(output)
    with _GLOBAL_LOCK:
        if force or _OVERWRITE_ALL or output_key in _CONFIRMED_OVERWRITES:
            return True

    if skip_existing or _SKIP_ALL:
        logger.info(f"File '{output}' already exists. Skipping.")
        return False

    if not sys.stdin.isatty():
        logger.warning(f"File '{output}' exists in non-interactive mode. Skipping.")
        return False

    prompt = f"File '{output}' exists. Overwrite? [y/n/a(ll)/i(skip all)]: "
    try:
        ans = input(prompt).lower().strip()
    except EOFError:
        logger.warning(f"File '{output}' exists but input is unavailable. Skipping.")
        return False
    if ans == "a":
        _OVERWRITE_ALL = True
        return True
    if ans == "i":
        _SKIP_ALL = True
        logger.info(f"File '{output}' already exists. Skipping.")
        return False
    if ans == "y":
        with _GLOBAL_LOCK:
            _CONFIRMED_OVERWRITES.add(output_key)
        return True
    return False


def safe_rename(
    tmp_path: str, output: str, force: bool = False, skip_existing: bool = False
) -> bool:
    """
    Safely moves the temporary file to the final destination.
    임시 파일을 최종 목적지로 안전하게 이동합니다.
    """
    if not should_download_output(output, force=force, skip_existing=skip_existing):
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False

    try:
        os.replace(tmp_path, output)
        return True
    except Exception as e:
        logger.error(f"Failed to save file '{output}': {e}")
        return False
    finally:
        with _GLOBAL_LOCK:
            _CONFIRMED_OVERWRITES.discard(os.path.abspath(output))
