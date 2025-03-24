#!/usr/bin/env python3
"""
easyget: wget/curl compatible file downloader
easyget: wget/curl 호환 파일 다운로드 도구

This script downloads files using HTTP with features similar to wget and curl.
이 스크립트는 wget과 curl과 유사한 기능을 제공하며 HTTP를 통해 파일을 다운로드합니다.

Features / 특징:
- Supports wget/curl style options (-O, -c, --limit-rate)
  wget/curl 스타일 옵션(-O, -c, --limit-rate)을 지원합니다.
- Supports wildcard (*) in URLs to expand and download multiple files from a directory listing.
  URL에 포함된 에스터리스크(*)를 확장하여 디렉토리 내 여러 파일을 다운로드할 수 있습니다.
- Accepts input as a txt, csv, or tsv file containing URLs and optional filenames.
  txt, csv, tsv 파일로부터 URL 및 파일명을 읽어들입니다.
- Displays two progress bars: one for total file count and one for the current file's progress.
  전체 파일 개수 진행바(상단)와 현재 파일 다운로드 진행바(하단)를 표시합니다.
- Uses httpx.Client for efficient HTTP connection reuse.
  효율적인 HTTP 연결 재사용을 위해 httpx.Client를 사용합니다.
- Provides detailed bilingual (English and Korean) comments for beginners.
  초보자도 쉽게 이해할 수 있도록 영어와 한국어로 상세한 주석을 제공합니다.
- All error messages are output in English prefixed with "easyget error:".
  모든 오류 메시지는 "easyget error:" 접두어와 함께 영어로 출력됩니다.
- After download completes, the final filename and file size are displayed.
  다운로드 완료 후 최종 파일명과 파일 용량(바이트 단위)을 출력합니다.
- Supports a new option to ignore cache (--no-cache) which forces a fresh download.
  새 옵션(--no-cache)을 통해 캐시(이전의 .part 파일)를 무시하고 새로 다운로드할 수 있습니다.
"""

import argparse
import os
import sys
import threading
import time
import logging
import base64
import csv
import fnmatch
import re
from urllib.parse import urlparse, urljoin
from typing import Optional, Dict, List, Tuple

import httpx
from tqdm import tqdm

# =============================
# Configuration Constants / 설정 상수
# =============================
CHUNK_SIZE = 1024 * 64  # 64KB - Size of each chunk to read / 청크 당 읽을 바이트 수
DEFAULT_THREADS = 4     # Default number of threads / 기본 스레드 수

# Global flags for file overwrite behavior / 파일 덮어쓰기 전역 변수
OVERWRITE_ALL = False  # If True, all existing files will be overwritten / 모든 파일 덮어쓰기 허용
SKIP_ALL = False       # If True, all existing files will be skipped / 모든 파일 건너뛰기

# Configure logging settings / 로깅 설정
logging.basicConfig(
    # format="[%(asctime)s] %(levelname)s: %(message)s",
    format="%(message)s",   # Simplified format / 간소화된 형식
    level=logging.INFO,  # INFO level logging / 정보 수준 로그
    # level=logging.DEBUG,  # DEBUG level logging (디버깅 수준 로그)
    # level=logging.ERROR,  # ERROR level logging (오류 수준 로그)
    datefmt="%H:%M:%S"
)


def get_filename_from_url(url: str) -> str:
    """
    Extract the filename from a URL.
    URL에서 파일명을 추출합니다.
    
    Parameters / 매개변수:
      - url (str): The URL to extract the filename from. / 파일명을 추출할 URL
      
    Returns / 반환값:
      - str: The extracted filename or a default name if not found. / 추출된 파일명 또는 찾지 못할 경우 기본 이름
    """
    path = urlparse(url).path  # Parse the URL path / URL 경로 파싱
    return os.path.basename(path) or "downloaded.file"  # Return basename or default / 기본 파일명 반환


def parse_speed(speed_str: str) -> Optional[int]:
    """
    Convert a speed limit string to bytes per second.
    속도 제한 문자열을 초당 바이트 단위로 변환합니다.
    
    Supported examples: '1M', '500K', '0.5M', etc.
    지원 예시: '1M', '500K', '0.5M' 등.
    
    If the value is less than 1, a warning is logged and None is returned.
    값이 1 미만인 경우 경고를 로그에 남기고 None을 반환합니다.
    
    Parameters / 매개변수:
      - speed_str (str): The speed limit string. / 속도 제한 문자열
      
    Returns / 반환값:
      - Optional[int]: Speed in bytes per second, or None if invalid. / 초당 바이트 단위 속도 또는 유효하지 않을 경우 None
    """
    try:
        speed_str = speed_str.strip().upper()  # Remove whitespace and convert to uppercase / 공백 제거 및 대문자 변환
        if speed_str.endswith("M"):
            speed = float(speed_str[:-1]) * 1024 * 1024
        elif speed_str.endswith("K"):
            speed = float(speed_str[:-1]) * 1024
        else:
            speed = float(speed_str)
        speed_int = int(speed)
        if speed_int < 1:
            logging.warning(f"Invalid speed limit '{speed_str}' provided. It must be at least 1 byte per second. No speed limit applied.")
            return None
        return speed_int
    except ValueError as e:
        logging.warning(f"Error parsing speed limit '{speed_str}': {e}. No speed limit applied.")
        return None


def get_file_size(url: str, headers: Dict[str, str], client: Optional[httpx.Client] = None) -> Optional[int]:
    """
    Retrieve the file size by sending an HTTP HEAD request.
    HTTP HEAD 요청을 보내 파일 크기를 가져옵니다.
    
    Parameters / 매개변수:
      - url (str): The URL of the file. / 파일의 URL
      - headers (Dict[str, str]): HTTP headers to include in the request. / 요청에 포함할 HTTP 헤더
      - client (Optional[httpx.Client]): Optional reusable HTTP client. / 선택 사항: 재사용 가능한 HTTP 클라이언트
      
    Returns / 반환값:
      - Optional[int]: The file size in bytes, or None if unavailable. / 파일 크기(바이트) 또는 사용 불가능할 경우 None
    """
    try:
        if client:
            response = client.head(url, headers=headers, follow_redirects=True, timeout=10)
        else:
            response = httpx.head(url, headers=headers, follow_redirects=True, timeout=10)
        if 'content-length' in response.headers:
            return int(response.headers['content-length'])
    except Exception as e:
        logging.error(f"easyget error: Failed to get file size: {e}")
    return None


def alias_wget_style(args: argparse.Namespace) -> argparse.Namespace:
    """
    Map wget style options (e.g., -O) to our arguments.
    wget 스타일 옵션(-O 등)을 명령행 인수에 매핑합니다.
    
    Parameters / 매개변수:
      - args (argparse.Namespace): Parsed command-line arguments. / 파싱된 명령행 인수
      
    Returns / 반환값:
      - argparse.Namespace: Updated arguments with wget style mappings. / wget 스타일 매핑이 적용된 인수
    """
    if args.output is None and '-O' in sys.argv:
        idx = sys.argv.index('-O')
        if idx + 1 < len(sys.argv):
            args.output = sys.argv[idx + 1]
    return args


def alias_wget_curl_style(args: argparse.Namespace) -> argparse.Namespace:
    """
    Map additional wget/curl style options.
    추가 wget/curl 스타일 옵션을 매핑합니다.
    
    Options mapped:
      - '-c' for resume (like wget) / '-c' 옵션은 이어받기(resume) 기능
      - '--limit-rate' for download speed limit / '--limit-rate' 옵션은 다운로드 속도 제한
      
    Parameters / 매개변수:
      - args (argparse.Namespace): Parsed command-line arguments. / 파싱된 명령행 인수
      
    Returns / 반환값:
      - argparse.Namespace: Updated arguments with additional mappings. / 추가 매핑이 적용된 인수
    """
    if not args.resume and '-c' in sys.argv:
        args.resume = True

    if args.max_speed is None:
        for arg in sys.argv:
            if arg.startswith("--limit-rate"):
                if "=" in arg:
                    _, value = arg.split("=", 1)
                    args.max_speed = value
                else:
                    idx = sys.argv.index(arg)
                    if idx + 1 < len(sys.argv):
                        args.max_speed = sys.argv[idx + 1]
    return args


class SpeedLimiter:
    """
    A class to limit download speed.
    다운로드 속도를 제한하기 위한 클래스입니다.
    
    Attributes / 속성:
      - max_speed (int): Maximum speed in bytes per second. / 초당 최대 속도 (바이트)
      - start_time (Optional[float]): Timestamp when download started. / 다운로드 시작 시간
      - downloaded (int): Total bytes downloaded so far. / 지금까지 다운로드한 총 바이트 수
    """
    def __init__(self, max_speed: int):
        self.max_speed = max_speed  # Set maximum speed / 최대 속도 설정
        self.start_time: Optional[float] = None
        self.downloaded = 0

    def wait(self, chunk_size: int) -> None:
        """
        Pause the download to maintain the speed limit.
        속도 제한을 유지하기 위해 다운로드를 일시 정지합니다.
        
        Parameters / 매개변수:
          - chunk_size (int): The size of the downloaded chunk in bytes. / 다운로드된 청크의 바이트 크기
        """
        if self.start_time is None:
            self.start_time = time.time()  # Set start time at first call / 첫 호출 시 시작 시간 설정
        self.downloaded += chunk_size
        elapsed = time.time() - self.start_time
        expected = self.downloaded / self.max_speed
        if expected > elapsed:
            time.sleep(expected - elapsed)  # Sleep for the remaining time / 남은 시간 동안 대기


def download_range(url: str, start: int, end: int, headers: Dict[str, str],
                   output_path: str, pbar: tqdm, limiter: Optional[SpeedLimiter] = None,
                   client: Optional[httpx.Client] = None,
                   error_event: Optional[threading.Event] = None) -> None:
    """
    Download a specific byte range of a file in a separate thread.
    별도의 스레드에서 파일의 특정 바이트 범위를 다운로드합니다.
    
    Parameters / 매개변수:
      - url (str): The URL of the file. / 파일의 URL
      - start (int): Starting byte of the range. / 시작 바이트
      - end (int): Ending byte of the range. / 종료 바이트
      - headers (Dict[str, str]): HTTP headers to use. / 사용할 HTTP 헤더
      - output_path (str): Path to the temporary output file. / 임시 출력 파일 경로
      - pbar (tqdm): Progress bar to update download progress. / 다운로드 진행 상황을 업데이트할 진행바
      - limiter (Optional[SpeedLimiter]): Optional speed limiter instance. / 선택 사항: 속도 제한 인스턴스
      - client (Optional[httpx.Client]): Optional reusable HTTP client. / 선택 사항: 재사용 가능한 HTTP 클라이언트
      - error_event (Optional[threading.Event]): Shared event to signal an error. / 오류 발생 시 공유 이벤트
    """
    range_header = headers.copy()
    range_header['Range'] = f'bytes={start}-{end}'  # Set the HTTP Range header / HTTP Range 헤더 설정
    try:
        if client:
            stream = client.stream("GET", url, headers=range_header, follow_redirects=True, timeout=30)
        else:
            stream = httpx.stream("GET", url, headers=range_header, follow_redirects=True, timeout=30)
        with stream as response:
            # Check HTTP status code before proceeding / 다운로드 시작 전 HTTP 상태 코드 확인
            if response.status_code >= 400:
                logging.error(f"easyget error: HTTP error {response.status_code} when downloading range {start}-{end} from {url}")
                if error_event:
                    error_event.set()
                return
            with open(output_path, 'r+b') as f:
                f.seek(start)  # Move file pointer to the start position / 파일 포인터를 시작 위치로 이동
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    if limiter:
                        limiter.wait(len(chunk))  # Apply speed limit if specified / 속도 제한 적용
                    f.write(chunk)  # Write the chunk to the file / 청크를 파일에 기록
                    pbar.update(len(chunk))  # Update progress bar / 진행바 업데이트
    except Exception as e:
        logging.error(f"easyget error: Error downloading range {start}-{end}: {e}")
        if error_event:
            error_event.set()


def safe_rename(tmp_path: str, output: str) -> bool:
    """
    Safely rename the temporary file to the final output filename.
    임시 파일을 최종 파일명으로 안전하게 변경합니다.
    
    If the output file already exists, prompt the user for action:
      - y: Overwrite this file.
      - n: Skip this file.
      - a: Overwrite all files.
      - i: Skip all files.
    
    파일이 이미 존재하는 경우 사용자에게 다음 옵션을 묻습니다:
      - y: 현재 파일 덮어쓰기
      - n: 현재 파일 건너뛰기
      - a: 모든 파일 덮어쓰기 허용
      - i: 모든 파일 건너뛰기
      
    Returns / 반환값:
      - bool: True if the file was successfully renamed (or overwritten), False if skipped.
              / 파일 변경(덮어쓰기) 성공 시 True, 건너뛰면 False.
    """
    global OVERWRITE_ALL, SKIP_ALL
    if os.path.exists(output):
        if SKIP_ALL:
            logging.error(f"easyget error: File {output} already exists. Skipping file.")
            os.remove(tmp_path)
            return False
        if not OVERWRITE_ALL:
            prompt = f"File '{output}' already exists. Overwrite? (y = yes, n = no, a = all yes, i = all no): "
            while True:
                answer = input(prompt).strip().lower()
                if answer == 'y':
                    break
                elif answer == 'n':
                    logging.error(f"easyget error: File {output} already exists. Skipping file.")
                    os.remove(tmp_path)
                    return False
                elif answer == 'a':
                    OVERWRITE_ALL = True
                    break
                elif answer == 'i':
                    SKIP_ALL = True
                    logging.error(f"easyget error: File {output} already exists. Skipping file.")
                    os.remove(tmp_path)
                    return False
                else:
                    print("Please enter y (yes), n (no), a (all yes), or i (all no).")
        try:
            os.remove(output)  # Remove existing file / 기존 파일 삭제
        except Exception as e:
            logging.error(f"easyget error: Failed to remove existing file {output}: {e}")
            os.remove(tmp_path)
            return False
    try:
        os.rename(tmp_path, output)  # Rename temporary file to final output / 임시 파일을 최종 파일명으로 변경
        return True
    except Exception as e:
        logging.error(f"easyget error: Failed to rename file from {tmp_path} to {output}: {e}")
        return False


def download_file(url: str, output: str, resume: bool = False, threads: int = DEFAULT_THREADS,
                  max_speed: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
                  progress_position: Optional[int] = None, client: Optional[httpx.Client] = None,
                  ignore_cache: bool = False) -> None:
    """
    Download a file from the given URL, supporting multi-threading, resume functionality,
    and an option to ignore cache.
    주어진 URL로부터 파일을 다운로드합니다. 멀티스레드, 이어받기 기능 및 캐시 무시 옵션을 지원합니다.
    
    Parameters / 매개변수:
      - url (str): The URL to download. / 다운로드할 URL
      - output (str): The output filename. / 저장할 파일명
      - resume (bool): Whether to resume an interrupted download. / 중단된 다운로드 이어받기 여부
      - threads (int): Number of threads for multi-threaded download. / 멀티스레드 다운로드에 사용할 스레드 수
      - max_speed (Optional[str]): Maximum download speed (e.g., "1M", "500K"). / 최대 다운로드 속도
      - headers (Optional[Dict[str, str]]): HTTP headers to use. / 사용할 HTTP 헤더
      - progress_position (Optional[int]): Position for the progress bar (tqdm). / 진행바 표시 위치
      - client (Optional[httpx.Client]): Optional reusable HTTP client. / 선택 사항: 재사용 가능한 HTTP 클라이언트
      - ignore_cache (bool): If True, ignore any existing cache (.part file) and force a fresh download.
                               If enabled, any existing temporary file is removed before starting download.
                               / True인 경우 기존 캐시(.part 파일)를 무시하고 새로 다운로드합니다.
                                 활성화되면 기존 임시 파일을 삭제하고 새로 다운로드합니다.
    """
    headers = headers or {}
    tmp_path = output + ".part"  # Temporary file path / 임시 파일 경로

    # If ignore_cache is enabled, remove any existing temporary file
    # 캐시 무시 옵션이 활성화된 경우, 기존의 임시 파일(.part 파일)을 삭제합니다.
    if ignore_cache and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
            logging.info(f"Ignoring cache: Removed existing temporary file {tmp_path}")
        except Exception as e:
            logging.error(f"easyget error: Failed to remove cache file {tmp_path}: {e}")

    total_size = get_file_size(url, headers, client=client)  # Get the total file size / 전체 파일 크기 확인

    # If file size is unknown, force single-threaded download
    if total_size is None:
        logging.info("File size unknown. Downloading using a single thread.")
        threads = 1

    # In multi-threaded resume, if resume is enabled, switch to single-thread mode
    if resume and threads > 1:
        logging.warning("easyget error: Resume feature may not work properly in multi-threaded mode. Switching to single thread.")
        threads = 1

    downloaded_size = 0
    mode = 'wb'
    if resume and os.path.exists(tmp_path):
        downloaded_size = os.path.getsize(tmp_path)
        if total_size and downloaded_size < total_size:
            headers['Range'] = f'bytes={downloaded_size}-'
            mode = 'ab'
        elif total_size and downloaded_size >= total_size:
            logging.info("The file appears to be fully downloaded already.")
            if safe_rename(tmp_path, output):
                try:
                    final_size = os.path.getsize(output)
                    logging.info(f"Download complete: {output} ({final_size} bytes)")
                except Exception as e:
                    logging.error(f"easyget error: Failed to get final file size for {output}: {e}")
            return

    limiter: Optional[SpeedLimiter] = None
    if max_speed:
        parsed_speed = parse_speed(max_speed)
        if parsed_speed:
            limiter = SpeedLimiter(parsed_speed)

    # Single-threaded download branch / 단일 스레드 다운로드 분기
    if threads == 1:
        try:
            if client:
                response = client.stream("GET", url, headers=headers, follow_redirects=True, timeout=60)
            else:
                response = httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60)
            with response as resp:
                # Check HTTP status code before downloading content / 다운로드 시작 전 HTTP 상태 코드 확인
                if resp.status_code >= 400:
                    logging.error(f"easyget error: HTTP error {resp.status_code} when downloading {url}")
                    return
                with open(tmp_path, mode) as f, tqdm(
                    total=total_size, initial=downloaded_size, unit='B', unit_scale=True,
                    desc=output, position=progress_position, leave=False
                ) as pbar:
                    for chunk in resp.iter_bytes(CHUNK_SIZE):
                        f.write(chunk)
                        pbar.update(len(chunk))
        except Exception as e:
            logging.error(f"easyget error: Download failed: {e}")
            return
    else:
        # Multi-threaded download branch / 멀티스레드 다운로드 분기
        if not os.path.exists(tmp_path):
            try:
                with open(tmp_path, 'wb') as f:
                    if total_size:
                        f.truncate(total_size)  # Pre-allocate file space / 파일 공간 미리 할당
            except Exception as e:
                logging.error(f"easyget error: Failed to create temporary file: {e}")
                return

        error_event = threading.Event()  # Shared event to signal error among threads / 스레드 간 오류 발생을 알리기 위한 공유 이벤트
        ranges: List[Tuple[int, int]] = []
        part_size = total_size // threads if total_size else 0
        for i in range(threads):
            start = i * part_size
            # Ensure the last thread gets any remaining bytes / 마지막 스레드가 남은 바이트를 받도록 설정
            end = total_size - 1 if total_size and i == threads - 1 else (start + part_size - 1)
            ranges.append((start, end))

        with tqdm(total=total_size, unit='B', unit_scale=True,
                  desc=output, position=progress_position, leave=False) as pbar:
            threads_list = []
            for start, end in ranges:
                t = threading.Thread(
                    target=download_range,
                    args=(url, start, end, headers, tmp_path, pbar, limiter, client, error_event)
                )
                threads_list.append(t)
                t.start()
            for t in threads_list:
                t.join()
        # If any thread encountered an error, abort the download / 스레드 중 하나라도 오류가 발생하면 다운로드를 중단합니다.
        if error_event.is_set():
            logging.error(f"easyget error: Download aborted due to HTTP errors while downloading {url}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return

    if safe_rename(tmp_path, output):
        try:
            final_size = os.path.getsize(output)
            logging.info(f"Download complete: {output} ({final_size} bytes)")
        except Exception as e:
            logging.error(f"easyget error: Failed to get final file size for {output}: {e}")


def parse_file_list(file_path: str) -> List[Tuple[str, str]]:
    """
    Parse an input file (txt, csv, or tsv) to extract a list of (URL, filename) tuples.
    txt, csv, tsv 파일을 파싱하여 (URL, filename) 튜플 리스트를 반환합니다.
    
    For txt files, each line is treated as a URL.
    txt 파일의 경우 각 줄을 URL로 처리합니다.
    
    For csv/tsv files, use the "url" column and the "filename" column (if available).
    csv/tsv 파일의 경우 "url" 컬럼과 "filename" 컬럼(존재할 경우)을 사용합니다.
    
    Parameters / 매개변수:
      - file_path (str): Path to the input file. / 입력 파일 경로
      
    Returns / 반환값:
      - List[Tuple[str, str]]: A list of tuples, each containing a URL and its corresponding filename.
                                / 각 튜플이 URL과 해당 파일명을 포함하는 리스트
    """
    file_list: List[Tuple[str, str]] = []
    ext = os.path.splitext(file_path)[1].lower()  # Get the file extension / 파일 확장자 추출
    try:
        with open(file_path, encoding='utf-8') as f:
            if ext == '.txt':
                for line in f:
                    line = line.strip()
                    if line:
                        file_list.append((line, get_filename_from_url(line)))
            elif ext in ['.csv', '.tsv']:
                delimiter = ',' if ext == '.csv' else '\t'  # Set delimiter based on file type / 파일 형식에 따른 구분자 설정
                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    url_val = row.get("url")
                    if not url_val:
                        continue
                    filename_val = row.get("filename") or get_filename_from_url(url_val)
                    file_list.append((url_val.strip(), filename_val.strip()))
            else:
                logging.error("easyget error: Unsupported file format. Supported formats: txt, csv, tsv.")
    except Exception as e:
        logging.error(f"easyget error: Failed to parse file list: {e}")
    return file_list


def expand_wildcard_url(url: str, headers: Dict[str, str], client: httpx.Client) -> List[Tuple[str, str]]:
    """
    If the URL contains an asterisk (*), expand it by retrieving the directory listing and matching the pattern.
    URL에 에스터리스크(*)가 포함된 경우, 디렉토리 목록을 가져와 패턴과 일치하는 파일 링크를 확장합니다.
    
    For example, given: http://example.com/files/*.zip
    예를 들어: http://example.com/files/*.zip
    
    Parameters / 매개변수:
      - url (str): The URL containing a wildcard. / 와일드카드가 포함된 URL
      - headers (Dict[str, str]): HTTP headers to use. / 사용할 HTTP 헤더
      - client (httpx.Client): Reusable HTTP client. / 재사용 가능한 HTTP 클라이언트
      
    Returns / 반환값:
      - List[Tuple[str, str]]: List of (full URL, filename) tuples for each matching file.
                                / 매칭된 각 파일에 대한 (전체 URL, 파일명) 튜플 리스트
    """
    parsed = urlparse(url)
    base_path = os.path.dirname(parsed.path)  # Extract directory path / 디렉토리 경로 추출
    pattern = os.path.basename(parsed.path)  # Extract the wildcard pattern / 와일드카드 패턴 추출
    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}/"  # Build base URL / 기본 URL 생성
    try:
        response = client.get(base_url, headers=headers, timeout=30)
        if response.status_code != 200:
            logging.error(f"easyget error: Failed to retrieve directory listing from {base_url} (Status code: {response.status_code})")
            return []
        links = re.findall(r'href="([^"]+)"', response.text)
        matched_links = [link for link in links if fnmatch.fnmatch(link, pattern)]
        file_list = []
        for link in matched_links:
            full_url = urljoin(base_url, link)
            filename = os.path.basename(link) or get_filename_from_url(full_url)
            file_list.append((full_url, filename))
        if not file_list:
            logging.error(f"easyget error: No files matching pattern '{pattern}' were found.")
        return file_list
    except Exception as e:
        logging.error(f"easyget error: Error during wildcard expansion: {e}")
        return []


def main() -> None:
    """
    Main function that parses command-line arguments and initiates the download process.
    명령행 인수를 파싱하고 다운로드 프로세스를 시작하는 메인 함수입니다.
    """
    parser = argparse.ArgumentParser(description="easyget: wget/curl compatible file downloader")
    parser.add_argument("input", help="URL to download or a file path (txt, csv, tsv) containing URLs (supports wildcard *)")
    parser.add_argument("-o", "--output", help="Output filename (for single URL download)")
    parser.add_argument("--resume", action="store_true", help="Resume interrupted download (equivalent to -c)")
    parser.add_argument("--multi", type=int, default=DEFAULT_THREADS, help="Number of threads for multi-threaded download")
    parser.add_argument("--max-speed", help="Maximum download speed (e.g., 1M, 500K)")
    parser.add_argument("--limit-rate", help="Wget style maximum download speed (e.g., 1M, 500K)")
    parser.add_argument("--user-agent", help="Specify the User-Agent header")
    parser.add_argument("--username", help="Username for basic authentication")
    parser.add_argument("--password", help="Password for basic authentication")
    parser.add_argument("--token", help="Bearer token for authentication")
    parser.add_argument("--header", action="append", help="Additional HTTP header (format: key:value)")
    # New option: ignore cache / 새 옵션: 캐시 무시 (이전 다운로드된 .part 파일 무시)
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached partial downloads and force a fresh download (ignore .part files)")
    
    args = parser.parse_args()
    args = alias_wget_style(args)
    args = alias_wget_curl_style(args)

    headers: Dict[str, str] = {}
    if args.username and args.password:
        userpass = f"{args.username}:{args.password}"
        headers['Authorization'] = 'Basic ' + base64.b64encode(userpass.encode()).decode()
    elif args.token:
        headers['Authorization'] = f"Bearer {args.token}"

    if args.user_agent:
        headers['User-Agent'] = args.user_agent

    if args.header:
        for h in args.header:
            if ':' in h:
                key, value = h.split(':', 1)
                headers[key.strip()] = value.strip()

    # Use httpx.Client for efficient HTTP requests / 효율적인 HTTP 요청을 위해 httpx.Client 사용
    with httpx.Client() as client:
        # If the input is a file (txt, csv, tsv), parse it for URLs / 입력이 파일인 경우 (txt, csv, tsv) URL 파싱
        if os.path.exists(args.input) and args.input.lower().endswith(('.txt', '.csv', '.tsv')):
            file_list = parse_file_list(args.input)
            if not file_list:
                logging.error("easyget error: No files to download. Please check your input file.")
                sys.exit(1)
            global_pbar = tqdm(total=len(file_list), desc="Total Files", position=0)
            for url, output in file_list:
                download_file(url, output, resume=args.resume, threads=args.multi,
                              max_speed=args.max_speed, headers=headers, progress_position=1, client=client,
                              ignore_cache=args.no_cache)
                global_pbar.update(1)
            global_pbar.close()
        # If the input URL contains a wildcard (*) expand it / URL에 와일드카드(*)가 포함된 경우 확장
        elif '*' in args.input:
            file_list = expand_wildcard_url(args.input, headers, client)
            if not file_list:
                logging.error("easyget error: No files found for wildcard expansion.")
                sys.exit(1)
            global_pbar = tqdm(total=len(file_list), desc="Total Files", position=0)
            for url, output in file_list:
                download_file(url, output, resume=args.resume, threads=args.multi,
                              max_speed=args.max_speed, headers=headers, progress_position=1, client=client,
                              ignore_cache=args.no_cache)
                global_pbar.update(1)
            global_pbar.close()
        else:
            # Single URL download / 단일 URL 다운로드
            url = args.input
            output = args.output or get_filename_from_url(url)
            download_file(url, output, resume=args.resume, threads=args.multi,
                          max_speed=args.max_speed, headers=headers, progress_position=0, client=client,
                          ignore_cache=args.no_cache)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Download interrupted by user.")
        sys.exit(1)
