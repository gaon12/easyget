import argparse
import base64
import json
import logging
import os
import sys
import urllib.parse
from typing import Any

from . import __version__
from .diagnostics import error_payload
from .downloader import download_file
from .exceptions import EasyGetError
from .input_parser import parse_file_list
from .logging_utils import setup_logging
from .session import Session
from .utils import ProgressBar, get_filename_from_url, parse_speed
from .wildcard import expand_wildcard_url

logger = logging.getLogger(__name__)

DEFAULT_THREADS = 4
EXIT_OK = 0
EXIT_PARTIAL = 3
EXIT_USAGE = 2
EXIT_NETWORK = 10
EXIT_HTTP = 11
EXIT_INTEGRITY = 12
EXIT_DOWNLOAD = 13
EXIT_INTERRUPTED = 130
EXIT_UNKNOWN = 1


def _render_success_payload(mode: str, result: Any, ai_mode: bool) -> dict[str, Any]:
    if ai_mode:
        return {"ok": 1, "m": mode, "r": result}
    return {"ok": True, "schema": "easyget/1", "mode": mode, "result": result}


def _render_results_payload(
    mode: str, results: list[dict[str, Any]], ai_mode: bool
) -> dict[str, Any]:
    success_count = sum(1 for item in results if item.get("status") == "success")
    total = len(results)
    if ai_mode:
        return {
            "ok": 1 if success_count == total else 0,
            "m": mode,
            "n": total,
            "s": success_count,
            "rs": results,
        }
    return {
        "ok": success_count == total,
        "schema": "easyget/1",
        "mode": mode,
        "summary": {
            "total": total,
            "success": success_count,
            "failed": total - success_count,
        },
        "results": results,
    }


def _print_payload(payload: dict[str, Any], ai_mode: bool) -> None:
    if ai_mode:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _exit_code_for_error(exc: Exception) -> int:
    if isinstance(exc, ValueError):
        return EXIT_USAGE
    if isinstance(exc, EasyGetError):
        if exc.code == "HTTP_STATUS_ERROR":
            return EXIT_HTTP
        if exc.code == "REQUEST_ERROR":
            return EXIT_NETWORK
        if exc.code == "INTEGRITY_ERROR":
            return EXIT_INTEGRITY
        if exc.code == "DOWNLOAD_FAILED":
            return EXIT_DOWNLOAD
    return EXIT_UNKNOWN


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value}")
    return ivalue


def _nonnegative_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(
            f"expected a non-negative integer, got {value}"
        )
    return ivalue


def _nonnegative_float(value: str) -> float:
    fvalue = float(value)
    if fvalue < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative number, got {value}")
    return fvalue


def _positive_float(value: str) -> float:
    fvalue = float(value)
    if fvalue <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive number, got {value}")
    return fvalue


def parse_args():
    parser = argparse.ArgumentParser(
        prog="easyget",
        description="easyget: wget/curl compatible file downloader (Python 3.12+ Zero-dependency)",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "input", help="URL to download or a file path (txt, csv, tsv) containing URLs"
    )
    parser.add_argument("-o", "-O", "--output", help="Output filename")
    parser.add_argument(
        "-c",
        "--continue",
        "--resume",
        dest="resume",
        action="store_true",
        help="Resume interrupted download",
    )
    parser.add_argument(
        "--multi",
        type=_positive_int,
        help="Number of threads (default: 4 in accurate mode, 1 in fast mode)",
    )
    parser.add_argument(
        "--retry",
        type=_nonnegative_int,
        default=3,
        help="Number of retries on failure (default: 3)",
    )
    parser.add_argument(
        "--retry-delay",
        type=_nonnegative_float,
        default=1.0,
        help="Base retry delay in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--retry-max-delay",
        type=_nonnegative_float,
        default=30.0,
        help="Maximum retry delay in seconds (default: 30)",
    )
    parser.add_argument(
        "--retry-backoff",
        choices=["exponential", "linear", "fixed"],
        default="exponential",
        help="Retry backoff strategy",
    )
    parser.add_argument(
        "--max-speed", "--limit-rate", help="Maximum speed (e.g., 1M, 500K)"
    )
    parser.add_argument("--user-agent", help="User-Agent header")
    parser.add_argument("--username", help="Username for basic auth")
    parser.add_argument("--password", help="Password for basic auth")
    parser.add_argument("--token", help="Bearer token for auth")
    parser.add_argument(
        "--header", action="append", help="Additional HTTP header (key:value)"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Ignore cached .part files"
    )
    parser.add_argument(
        "--timestamping",
        action="store_true",
        help="Skip download when local file is newer than remote Last-Modified",
    )
    parser.add_argument(
        "--mode",
        choices=["fast", "accurate"],
        default="fast",
        help="Download mode (default: fast)",
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite existing files"
    )
    parser.add_argument(
        "-s", "--skip-existing", action="store_true", help="Skip existing files"
    )
    parser.add_argument("-P", "--output-dir", help="Directory to save files")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Quiet mode (no output)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )
    parser.add_argument(
        "--ai", action="store_true", help="Emit compact, token-optimized machine output"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Display debug logs"
    )
    parser.add_argument(
        "-X",
        "--request-method",
        help="HTTP method for request mode (e.g., GET, POST, PUT)",
    )
    parser.add_argument(
        "-d", "--data", dest="request_data", help="HTTP request body for request mode"
    )
    parser.add_argument(
        "--data-binary",
        help="Raw binary request body; @path reads the body from a file",
    )
    parser.add_argument("--json-data", help="JSON request body string for request mode")
    parser.add_argument(
        "-I",
        "--head",
        dest="head_only",
        action="store_true",
        help="Use HTTP HEAD in request mode",
    )
    parser.add_argument(
        "-L",
        "--location",
        action="store_true",
        help="Follow redirects in request mode (downloads always follow redirects)",
    )
    parser.add_argument(
        "--fail",
        dest="fail_http",
        action="store_true",
        help="Fail on HTTP 4xx/5xx in request mode",
    )
    parser.add_argument(
        "-i",
        "--include",
        dest="include_headers",
        action="store_true",
        help="Include response headers in output",
    )
    parser.add_argument(
        "--data-urlencode",
        action="append",
        help="URL-encoded data field (request mode)",
    )
    parser.add_argument(
        "-F",
        "--form",
        action="append",
        help="Multipart form field (request mode), e.g., key=value or file=@/path",
    )
    parser.add_argument("--proxy", help="Proxy URL for request mode")
    parser.add_argument(
        "--cacert", help="CA bundle path for TLS verification in request mode"
    )
    parser.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        help="Disable TLS verification in request mode",
    )
    parser.add_argument(
        "--cert", help="Client certificate path for mTLS in request mode"
    )
    parser.add_argument(
        "--key", help="Client private key path for mTLS in request mode"
    )
    parser.add_argument(
        "--compressed",
        action="store_true",
        help="Request compressed response and auto-decompress",
    )
    parser.add_argument(
        "--output-select",
        choices=["all", "status", "headers", "body"],
        default="all",
        help="Select response fields to output in request mode",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30)",
    )

    return parser.parse_args()


def is_request_mode(args: argparse.Namespace) -> bool:
    return any(
        [
            args.request_method,
            args.request_data is not None,
            args.data_binary is not None,
            args.json_data is not None,
            bool(args.data_urlencode),
            bool(args.form),
            args.head_only,
            args.fail_http,
            args.include_headers,
            args.proxy,
            args.cacert,
            args.insecure,
            args.cert,
            args.key,
            args.compressed,
            args.output_select != "all",
        ]
    )


def _select_request_payload(
    response, method: str, output: str | None, select: str, ai_mode: bool
) -> dict[str, Any]:
    if ai_mode:
        base = {"m": method, "o": output}
    else:
        base = {"method": method, "output": output}

    if select == "status":
        if ai_mode:
            base["st"] = response.status_code
        else:
            base["status"] = response.status_code
        return base

    if select == "headers":
        if ai_mode:
            base["h"] = response.headers
        else:
            base["headers"] = response.headers
        return base

    if select == "body":
        if ai_mode:
            base["b"] = None if output else response.text
        else:
            base["body"] = None if output else response.text
        return base

    summary = response.summary(
        include_body=not bool(output), max_body_chars=4096, compact=ai_mode
    )
    if ai_mode:
        return {**base, **summary}
    return {
        **base,
        "status": summary["status_code"],
        "ok": summary["ok"],
        "url": summary["url"],
        "headers": summary["headers"],
        "body": None if output else summary.get("body_preview"),
    }


def _parse_data_urlencode(values: list[str]) -> str:
    encoded_parts = []
    for raw in values:
        if "=" in raw:
            key, value = raw.split("=", 1)
            encoded_parts.append(
                f"{urllib.parse.quote_plus(key)}={urllib.parse.quote_plus(value)}"
            )
        else:
            encoded_parts.append(urllib.parse.quote_plus(raw))
    return "&".join(encoded_parts)


def _parse_form_entries(
    values: list[str],
) -> tuple[list[tuple[str, str]], dict[str, tuple]]:
    data_fields: list[tuple[str, str]] = []
    file_fields: dict[str, tuple] = {}

    for raw in values:
        if "=" not in raw:
            raise ValueError(f"Invalid form field: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid form field key: {raw}")

        if value.startswith("@"):
            file_spec = value[1:]
            content_type = None
            if ";type=" in file_spec:
                file_path, content_type = file_spec.split(";type=", 1)
            else:
                file_path = file_spec
            if not os.path.exists(file_path):
                raise ValueError(f"Form file not found: {file_path}")
            with open(file_path, "rb") as f:
                file_bytes = f.read()
            filename = os.path.basename(file_path) or key
            if content_type:
                file_fields[key] = (filename, file_bytes, content_type)
            else:
                file_fields[key] = (filename, file_bytes)
        else:
            data_fields.append((key, value))

    return data_fields, file_fields


def _download_kwargs(
    args: argparse.Namespace,
    headers: dict[str, str],
    threads: int,
    show_progress: bool,
    position: int,
) -> dict[str, Any]:
    return {
        "resume": args.resume,
        "threads": threads,
        "max_speed": args.max_speed,
        "headers": headers,
        "progress_position": position,
        "ignore_cache": args.no_cache,
        "mode": args.mode,
        "force": args.force,
        "skip_existing": args.skip_existing,
        "retries": args.retry,
        "show_progress": show_progress,
        "retry_delay": args.retry_delay,
        "retry_backoff": args.retry_backoff,
        "retry_max_delay": args.retry_max_delay,
        "timestamping": args.timestamping,
        "timeout": args.timeout,
    }


def run_request_mode(args: argparse.Namespace, headers: dict[str, str]) -> int:
    if os.path.exists(args.input) and args.input.lower().endswith(
        (".txt", ".csv", ".tsv")
    ):
        raise ValueError(
            "Request mode does not support URL list files. Provide a single URL."
        )
    if "*" in args.input:
        raise ValueError(
            "Request mode does not support wildcard URLs. Provide a single URL."
        )

    if args.request_method:
        method = args.request_method.upper()
    elif args.head_only:
        method = "HEAD"
    elif (
        args.request_data is not None
        or args.data_binary is not None
        or args.json_data is not None
        or args.data_urlencode
        or args.form
    ):
        method = "POST"
    else:
        method = "GET"

    json_payload = None
    if args.json_data is not None:
        if args.request_data is not None or args.data_urlencode:
            raise ValueError(
                "--json-data cannot be combined with --data/--data-urlencode"
            )
        try:
            json_payload = json.loads(args.json_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in --json-data: {e}") from e

    if args.key and not args.cert:
        raise ValueError("--key requires --cert")
    if args.insecure and args.cacert:
        raise ValueError("Cannot use --insecure and --cacert together")
    if args.proxy:
        proxy_scheme = urllib.parse.urlsplit(args.proxy).scheme.lower()
        if proxy_scheme not in ("http", "https"):
            raise ValueError("--proxy must be an http:// or https:// URL")
    for opt_value, opt_name in (
        (args.cacert, "--cacert"),
        (args.cert, "--cert"),
        (args.key, "--key"),
    ):
        if opt_value and not os.path.exists(opt_value):
            raise ValueError(f"{opt_name} file not found: {opt_value}")

    form_data = None
    form_files = None
    if args.form:
        if (
            args.request_data is not None
            or args.data_urlencode
            or json_payload is not None
        ):
            raise ValueError(
                "--form cannot be combined with --data/--data-urlencode/--json-data"
            )
        form_data, form_files = _parse_form_entries(args.form)

    request_data: str | bytes | None = args.request_data
    if args.data_binary is not None:
        if (
            args.request_data is not None
            or args.data_urlencode
            or json_payload is not None
            or args.form
        ):
            raise ValueError(
                "--data-binary cannot be combined with "
                "--data/--data-urlencode/--json-data/--form"
            )
        if args.data_binary.startswith("@"):
            binary_path = args.data_binary[1:]
            if not os.path.exists(binary_path):
                raise ValueError(f"--data-binary file not found: {binary_path}")
            with open(binary_path, "rb") as f:
                request_data = f.read()
        else:
            request_data = args.data_binary

    if args.data_urlencode:
        encoded = _parse_data_urlencode(args.data_urlencode)
        request_data = f"{request_data}&{encoded}" if request_data else encoded
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    verify = None
    if args.insecure:
        logger.warning(
            "TLS verification is disabled (-k/--insecure). Connections are not secure."
        )
        verify = False
    elif args.cacert:
        verify = args.cacert

    cert = None
    if args.cert and args.key:
        cert = (args.cert, args.key)
    elif args.cert:
        cert = args.cert

    with Session() as session:
        response = session.request(
            method=method,
            url=args.input,
            data=form_data if args.form else request_data,
            json=json_payload,
            files=form_files,
            headers=headers,
            timeout=args.timeout,
            allow_redirects=args.location,
            verify=verify,
            cert=cert,
            proxies=args.proxy,
            compressed=args.compressed,
            stream=False,
        )

    if args.fail_http:
        response.raise_for_status()

    body_bytes = b"" if method == "HEAD" else response.content

    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "wb") as f:
            f.write(body_bytes)

    if args.json:
        payload_data = _select_request_payload(
            response,
            method=method,
            output=args.output,
            select=args.output_select,
            ai_mode=args.ai,
        )
        payload = _render_success_payload("request", payload_data, ai_mode=args.ai)
        _print_payload(payload, ai_mode=args.ai)
        return EXIT_OK

    if args.output_select in {"headers", "all"} or args.include_headers:
        print(f"HTTP {response.status_code}")
        for key, value in response.headers.items():
            print(f"{key}: {value}")
        print()
        if args.output_select == "headers":
            return EXIT_OK

    if args.output_select == "status":
        print(response.status_code)
        return EXIT_OK

    if args.output_select in {"body", "all"} and not args.output and body_bytes:
        content_type = response.headers.get("Content-Type", "")
        is_textual = content_type.startswith("text/") or any(
            marker in content_type
            for marker in (
                "json",
                "xml",
                "javascript",
                "x-www-form-urlencoded",
                "charset",
            )
        )
        if is_textual:
            sys.stdout.write(response.text)
            if not response.text.endswith("\n"):
                sys.stdout.write("\n")
        else:
            # Binary bodies go to stdout raw so they survive shell redirection.
            sys.stdout.buffer.write(body_bytes)
            sys.stdout.buffer.flush()
    return EXIT_OK


def main():
    args = parse_args()
    if args.ai:
        args.json = True
    setup_logging(args.verbose, args.quiet or args.json)

    # Disable progress bars in quiet or json mode
    show_progress = not (args.quiet or args.json)
    threads = args.multi
    if threads is None:
        threads = DEFAULT_THREADS if args.mode == "accurate" else 1

    headers: dict[str, str] = {}
    if args.username and args.password:
        userpass = f"{args.username}:{args.password}"
        headers["Authorization"] = (
            f"Basic {base64.b64encode(userpass.encode()).decode()}"
        )
    elif args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    if args.user_agent:
        headers["User-Agent"] = args.user_agent
    if args.header:
        for h in args.header:
            if ":" in h:
                key, value = h.split(":", 1)
                # Strip CR/LF so a header value can't inject extra header lines.
                safe_key = key.strip().replace("\r", "").replace("\n", "")
                safe_value = value.strip().replace("\r", "").replace("\n", "")
                if safe_key:
                    headers[safe_key] = safe_value
                else:
                    logger.warning(f"Ignoring malformed --header (empty name): {h}")
            else:
                logger.warning(
                    f"Ignoring malformed --header (expected 'Key: Value'): {h}"
                )
    if args.head_only:
        args.request_method = "HEAD"

    try:
        if args.max_speed and parse_speed(args.max_speed) is None:
            raise ValueError(f"Invalid --max-speed value: {args.max_speed}")

        if is_request_mode(args):
            sys.exit(run_request_mode(args, headers))

        file_list: list[tuple[str, str]] = []
        if os.path.exists(args.input) and args.input.lower().endswith(
            (".txt", ".csv", ".tsv")
        ):
            file_list = parse_file_list(args.input)
            if args.output:
                logger.warning(
                    "-o/--output is ignored when downloading from a URL list "
                    "file; use -P/--output-dir or a 'filename' column instead."
                )
        elif "*" in args.input:
            file_list = expand_wildcard_url(args.input, headers)
        else:
            url = args.input
            output = args.output or get_filename_from_url(url)
            file_list = [(url, output)]

        if not file_list:
            logger.error("easyget error: No files to download.")
            sys.exit(1)

        if len(file_list) > 1:
            global_pbar = (
                ProgressBar(
                    total=len(file_list), desc="Total Files", position=0, unit="files"
                )
                if show_progress
                else None
            )
            results = []
            success_count = 0
            for i, (url, output) in enumerate(file_list):
                dest = (
                    os.path.join(args.output_dir, output) if args.output_dir else output
                )

                try:
                    info = download_file(
                        url,
                        dest,
                        **_download_kwargs(
                            args, headers, threads, show_progress, position=1
                        ),
                    )
                    success_count += 1
                    item = {
                        "url": url,
                        "output": dest,
                        "status": "success",
                        "ok": True,
                    }
                    if isinstance(info, dict):
                        item["bytes"] = info.get("bytes")
                        item["skipped"] = info.get("skipped")
                    results.append(item)
                except Exception as e:
                    if not args.json:
                        logger.error(f"\nFailed to download {url}: {e}")
                    err = error_payload(e, compact=args.ai)
                    item = {"url": url, "output": dest, "status": "error", "ok": False}
                    item.update(err)
                    results.append(item)

                if global_pbar:
                    global_pbar.update(1)

            if global_pbar:
                global_pbar.close()

            if args.json:
                payload = _render_results_payload("download", results, ai_mode=args.ai)
                _print_payload(payload, ai_mode=args.ai)
            elif not args.quiet:
                logger.info(
                    f"Batch download complete: {success_count}/{len(file_list)} files successful."
                )
            if success_count != len(file_list):
                # Partial failures must surface in the exit code in every
                # output mode — scripts relying on $? shouldn't need --json.
                sys.exit(EXIT_PARTIAL)
        else:
            url, output = file_list[0]
            if args.output_dir:
                output = os.path.join(args.output_dir, output)

            try:
                info = download_file(
                    url,
                    output,
                    **_download_kwargs(
                        args, headers, threads, show_progress, position=0
                    ),
                )
                if args.json:
                    result = {
                        "url": url,
                        "output": output,
                        "status": "success",
                        "ok": True,
                    }
                    if isinstance(info, dict):
                        result["bytes"] = info.get("bytes")
                        result["skipped"] = info.get("skipped")
                    payload = _render_success_payload(
                        "download", result, ai_mode=args.ai
                    )
                    _print_payload(payload, ai_mode=args.ai)
            except Exception as e:
                if args.json:
                    err = error_payload(e, compact=args.ai)
                    if args.ai:
                        payload = {
                            "ok": 0,
                            "m": "download",
                            "r": {"u": url, "o": output, "s": "error"},
                            "e": err.get("e", {"c": "UNEXPECTED_ERROR", "m": str(e)}),
                        }
                    else:
                        payload = {
                            "ok": False,
                            "schema": "easyget/1",
                            "mode": "download",
                            "result": {"url": url, "output": output, "status": "error"},
                            "error": err.get(
                                "error", {"code": "UNEXPECTED_ERROR", "message": str(e)}
                            ),
                        }
                    _print_payload(payload, ai_mode=args.ai)
                    sys.exit(_exit_code_for_error(e))
                raise
    except KeyboardInterrupt:
        logger.info("\nDownload interrupted by user.")
        sys.exit(EXIT_INTERRUPTED)
    except Exception as e:
        if args.json:
            payload = error_payload(e, compact=args.ai)
            _print_payload(payload, ai_mode=args.ai)
            sys.exit(_exit_code_for_error(e))
        logger.error(f"easyget error: {e}")
        sys.exit(_exit_code_for_error(e))
