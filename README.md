# easyget

`easyget` is a wget/curl-compatible command-line downloader written in Python.  
It supports modern features like multithreading, speed limits, resume support, wildcard URL expansion, and progress bars — all in a user-friendly bilingual (English + Korean) interface.

`easyget`는 Python으로 작성된 wget/curl 호환 명령줄 다운로드 도구입니다.  
멀티스레드, 속도 제한, 이어받기, 와일드카드 URL 확장, 진행률 표시 등 현대적인 기능을 지원하며, 영어와 한국어로 모두 친절하게 안내합니다.

---

## Features / 특징

- ✅ **wget/curl-style options** (`-O`, `-c`, `--limit-rate`)
- ✅ **Multithreaded downloads** (default 4 threads)
- ✅ **Speed limits** (e.g., `--max-speed 1M`)
- ✅ **Resume support** (`--resume` or `-c`)
- ✅ **Wildcard (*) expansion in URLs** (parsing directory listings)
- ✅ **Input from txt, csv, tsv files**
- ✅ **Progress bars** (total file count and individual download progress)
- ✅ **Ignore cache** (`--no-cache`, ignore `.part` files)
- ✅ **Basic auth / Bearer token support**
- ✅ **Download modes** (`--mode fast` or `accurate`)
  - `fast` (default): single-threaded, skips HEAD inquiry for speed
  - `accurate`: prefers HEAD request to get Content-Length and uses multithread when possible
- ✅ **English/Korean comments and error messages**

- ✅ **wget/curl 스타일 옵션 지원** (`-O`, `-c`, `--limit-rate`)
- ✅ **멀티스레드 다운로드** (기본 4개 스레드)
- ✅ **속도 제한** (e.g., `--max-speed 1M`)
- ✅ **이어받기 지원** (`--resume` 또는 `-c`)
- ✅ **URL 내 와일드카드(*) 확장 지원** (디렉토리 리스트 파싱)
- ✅ **txt, csv, tsv 파일 입력 지원**
- ✅ **진행률 표시** (총 파일 수 및 개별 파일 다운로드)
- ✅ **캐시 무시 기능** (`--no-cache`, `.part` 파일 무시)
- ✅ **기본 인증 / Bearer 토큰 지원**
- ✅ **다운로드 모드** (`--mode fast` 또는 `accurate`)
  - `fast` (기본값): HEAD 조회를 생략하여 단일 스레드로 빠르게 다운로드
  - `accurate`: HEAD 요청을 통해 Content-Length를 확인하고, 가능하다면 멀티스레드 다운로드
- ✅ **영어/한글 주석 및 에러 메시지**

---

## Installation / 설치

```bash
pip install httpx tqdm easyget
```

Or go to [Release page](https://github.com/gaon12/easyget/releases/latest) and download the latest version. When using the build file, you don't need to install `httpx` and `tqdm`. Also, don't add `python` before `easyget.py`. Just type `easyget` and options.

또는 [릴리즈 페이지](https://github.com/gaon12/easyget/releases/latest)에서 최신 버전을 다운로드하세요. 빌드 파일을 사용할 때는 `httpx`와 `tqdm`를 설치할 필요가 없습니다. 또한 `easyget.py` 앞에 `python` 명령을 추가하지 마세요. 그냥 `easyget`과 옵션을 입력하세요.

---

## Usage / 사용법

### 1. Single file download / 단일 파일 다운로드

```bash
python easyget.py "https://example.com/file.zip"
```

### 2. Specify mode / 모드 지정 (fast or accurate)

```bash
python easyget.py --mode accurate "https://example.com/file.zip"
```

### 3. Specify output filename / 파일명 지정

```bash
python easyget.py "https://example.com/file.zip" -O myfile.zip
```

### 4. Resume download / 이어받기

```bash
python easyget.py "https://example.com/file.zip" -c
```

### 5. Multi-threaded with speed limit / 멀티스레드 + 속도 제한

```bash
python easyget.py "https://example.com/large.iso" --mode accurate --multi 8 --max-speed 2M
```

### 6. Use input file list (txt, csv, tsv) / URL 리스트로 다운로드

```bash
python easyget.py urls.csv
```

### 7. Wildcard in URL / 와일드카드 URL 사용

```bash
python easyget.py "https://example.com/files/*.zip"
```

---

## Input File Format / 입력 파일 형식

### txt

```
https://example.com/file1.zip
https://example.com/file2.zip
```

### csv or tsv

| url                         | filename         |
|----------------------------|------------------|
| https://example.com/a.pdf  | lecture_a.pdf    |
| https://example.com/b.pdf  | lecture_b.pdf    |

---

## Advanced Options / 고급 옵션

| Option                   | Description / 설명 |
|--------------------------|---------------------|
| `--output`, `-O`         | Output file name (단일 파일 다운로드 시) |
| `--resume`, `-c`         | Resume download (이어받기) |
| `--multi`                | Number of threads (스레드 수) |
| `--max-speed`            | Download speed limit (e.g., 1M, 500K) |
| `--no-cache`             | Ignore .part cache and force redownload |
| `--header`               | Custom HTTP headers (e.g., `"Key: Value"`) |
| `--user-agent`           | User-Agent 설정 |
| `--username`, `--password` | Basic 인증용 계정 정보 |
| `--token`                | Bearer 토큰 인증 |
| `--mode`                 | Download mode: `fast` or `accurate` |

---

## Notes / 주의사항

- When a download is interrupted, a `.part` file is created.
- Use `--no-cache` to ignore existing `.part` files and redownload.
- Wildcard URLs are based on `href="..."` format in HTML directory listings.

- 다운로드가 중단되면 `.part` 파일이 생성됩니다.
- `--no-cache` 옵션을 사용하면 기존 `.part` 파일을 무시하고 새로 다운로드합니다.
- 와일드카드 URL은 HTML 디렉토리 리스트에서 `href="..."` 형식을 기반으로 파일을 찾습니다.

---

## License

This project is licensed under the [MIT License](./LICENSE).

이 프로젝트는 [MIT 라이선스](./LICENSE)를 따릅니다。

---

## Contribute / 기여

For questions or improvements, feel free to open an issue or pull request!

기여하고 싶거나 궁금한 점이 있으면 언제든 이슈나 PR을 열어주세요！