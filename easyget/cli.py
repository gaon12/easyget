import argparse
import sys
import logging
import base64
import os
import json
import urllib.parse
from typing import Dict, List, Tuple

from .logging_utils import setup_logging
from .utils import get_filename_from_url, ProgressBar
from .input_parser import parse_file_list
from .wildcard import expand_wildcard_url
from .downloader import download_file
from .session import Session

DEFAULT_THREADS = 4

def parse_args():
    parser = argparse.ArgumentParser(description="easyget: wget/curl compatible file downloader (Python 3.12+ Zero-dependency)")
    parser.add_argument("input", help="URL to download or a file path (txt, csv, tsv) containing URLs")
    parser.add_argument("-o", "-O", "--output", help="Output filename")
    parser.add_argument("-c", "--resume", action="store_true", help="Resume interrupted download")
    parser.add_argument("--multi", type=int, help="Number of threads (default: 4 in accurate mode, 1 in fast mode)")
    parser.add_argument("--retry", type=int, default=3, help="Number of retries on failure (default: 3)")
    parser.add_argument("--max-speed", "--limit-rate", help="Maximum speed (e.g., 1M, 500K)")
    parser.add_argument("--user-agent", help="User-Agent header")
    parser.add_argument("--username", help="Username for basic auth")
    parser.add_argument("--password", help="Password for basic auth")
    parser.add_argument("--token", help="Bearer token for auth")
    parser.add_argument("--header", action="append", help="Additional HTTP header (key:value)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached .part files")
    parser.add_argument("--mode", choices=["fast", "accurate"], default="fast", help="Download mode (default: fast)")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("-s", "--skip-existing", action="store_true", help="Skip existing files")
    parser.add_argument("-P", "--output-dir", help="Directory to save files")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (no output)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("-v", "--verbose", action="store_true", help="Display debug logs")
    parser.add_argument("-X", "--request-method", help="HTTP method for request mode (e.g., GET, POST, PUT)")
    parser.add_argument("-d", "--data", dest="request_data", help="HTTP request body for request mode")
    parser.add_argument("--json-data", help="JSON request body string for request mode")
    parser.add_argument("-I", "--head", dest="head_only", action="store_true", help="Use HTTP HEAD in request mode")
    parser.add_argument("-L", "--location", action="store_true", help="Follow redirects in request mode")
    parser.add_argument("--fail", dest="fail_http", action="store_true", help="Fail on HTTP 4xx/5xx in request mode")
    parser.add_argument("-i", "--include", dest="include_headers", action="store_true", help="Include response headers in output")
    parser.add_argument("--data-urlencode", action="append", help="URL-encoded data field (request mode)")
    parser.add_argument("-F", "--form", action="append", help="Multipart form field (request mode), e.g., key=value or file=@/path")
    parser.add_argument("--proxy", help="Proxy URL for request mode")
    parser.add_argument("--cacert", help="CA bundle path for TLS verification in request mode")
    parser.add_argument("-k", "--insecure", action="store_true", help="Disable TLS verification in request mode")
    parser.add_argument("--cert", help="Client certificate path for mTLS in request mode")
    parser.add_argument("--key", help="Client private key path for mTLS in request mode")
    parser.add_argument("--compressed", action="store_true", help="Request compressed response and auto-decompress")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout (seconds) for request mode")

    return parser.parse_args()

def is_request_mode(args: argparse.Namespace) -> bool:
    return any([
        args.request_method,
        args.request_data is not None,
        args.json_data is not None,
        bool(args.data_urlencode),
        bool(args.form),
        args.head_only,
        args.location,
        args.fail_http,
        args.include_headers,
        args.proxy,
        args.cacert,
        args.insecure,
        args.cert,
        args.key,
        args.compressed,
    ])

def _parse_data_urlencode(values: List[str]) -> str:
    encoded_parts = []
    for raw in values:
        if "=" in raw:
            key, value = raw.split("=", 1)
            encoded_parts.append(f"{urllib.parse.quote_plus(key)}={urllib.parse.quote_plus(value)}")
        else:
            encoded_parts.append(urllib.parse.quote_plus(raw))
    return "&".join(encoded_parts)

def _parse_form_entries(values: List[str]) -> Tuple[List[Tuple[str, str]], Dict[str, tuple]]:
    data_fields: List[Tuple[str, str]] = []
    file_fields: Dict[str, tuple] = {}

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

def run_request_mode(args: argparse.Namespace, headers: Dict[str, str]) -> int:
    if os.path.exists(args.input) and args.input.lower().endswith(('.txt', '.csv', '.tsv')):
        raise ValueError("Request mode does not support URL list files. Provide a single URL.")
    if '*' in args.input:
        raise ValueError("Request mode does not support wildcard URLs. Provide a single URL.")

    if args.request_method:
        method = args.request_method.upper()
    elif args.head_only:
        method = "HEAD"
    elif args.request_data is not None or args.json_data is not None or args.data_urlencode or args.form:
        method = "POST"
    else:
        method = "GET"

    json_payload = None
    if args.json_data is not None:
        try:
            json_payload = json.loads(args.json_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in --json-data: {e}") from e

    if args.key and not args.cert:
        raise ValueError("--key requires --cert")
    if args.insecure and args.cacert:
        raise ValueError("Cannot use --insecure and --cacert together")

    form_data = None
    form_files = None
    if args.form:
        if args.request_data is not None or args.data_urlencode or json_payload is not None:
            raise ValueError("--form cannot be combined with --data/--data-urlencode/--json-data")
        form_data, form_files = _parse_form_entries(args.form)

    request_data = args.request_data
    if args.data_urlencode:
        encoded = _parse_data_urlencode(args.data_urlencode)
        if request_data:
            request_data = f"{request_data}&{encoded}"
        else:
            request_data = encoded
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    verify = None
    if args.insecure:
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
        payload = {
            "url": response.url,
            "method": method,
            "status": response.status_code,
            "headers": response.headers,
            "output": args.output,
            "body": None if args.output else response.text,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.include_headers:
        print(f"HTTP {response.status_code}")
        for key, value in response.headers.items():
            print(f"{key}: {value}")
        print()

    if not args.output and body_bytes:
        sys.stdout.write(response.text)
        if not response.text.endswith("\n"):
            sys.stdout.write("\n")
    return 0

def main():
    args = parse_args()
    setup_logging(args.verbose, args.quiet or args.json)
    
    # Disable progress bars in quiet or json mode
    show_progress = not (args.quiet or args.json)
    threads = args.multi
    if threads is None:
        threads = DEFAULT_THREADS if args.mode == "accurate" else 1

    headers: Dict[str, str] = {}
    if args.username and args.password:
        userpass = f"{args.username}:{args.password}"
        headers['Authorization'] = f'Basic {base64.b64encode(userpass.encode()).decode()}'
    elif args.token:
        headers['Authorization'] = f"Bearer {args.token}"

    if args.user_agent: headers['User-Agent'] = args.user_agent
    if args.header:
        for h in args.header:
            if ':' in h:
                key, value = h.split(':', 1)
                headers[key.strip()] = value.strip()
    if args.head_only:
        args.request_method = "HEAD"

    try:
        if is_request_mode(args):
            sys.exit(run_request_mode(args, headers))

        file_list: List[Tuple[str, str]] = []
        if os.path.exists(args.input) and args.input.lower().endswith(('.txt', '.csv', '.tsv')):
            file_list = parse_file_list(args.input)
        elif '*' in args.input:
            file_list = expand_wildcard_url(args.input, headers)
        else:
            url = args.input
            output = args.output or get_filename_from_url(url)
            file_list = [(url, output)]

        if not file_list:
            logging.error("easyget error: No files to download.")
            sys.exit(1)

        if len(file_list) > 1:
            global_pbar = ProgressBar(total=len(file_list), desc="Total Files", position=0, unit="files") if show_progress else None
            results = []
            success_count = 0
            for i, (url, output) in enumerate(file_list):
                if args.output_dir:
                    output = os.path.join(args.output_dir, output)
                
                try:
                    download_file(url, output, resume=args.resume, threads=threads,
                                  max_speed=args.max_speed, headers=headers, progress_position=1,
                                  ignore_cache=args.no_cache, mode=args.mode, force=args.force,
                                  skip_existing=args.skip_existing, retries=args.retry,
                                  show_progress=show_progress)
                    success_count += 1
                    results.append({"url": url, "output": output, "status": "success"})
                except Exception as e:
                    if not args.json:
                        logging.error(f"\nFailed to download {url}: {e}")
                    results.append({"url": url, "output": output, "status": "error", "message": str(e)})
                
                if global_pbar: global_pbar.update(1)
            
            if global_pbar: global_pbar.close()
            
            if args.json:
                import json
                print(json.dumps(results, indent=2))
                if success_count != len(file_list):
                    sys.exit(1)
            elif not args.quiet:
                logging.info(f"Batch download complete: {success_count}/{len(file_list)} files successful.")
        else:
            url, output = file_list[0]
            if args.output_dir:
                output = os.path.join(args.output_dir, output)
            
            try:
                download_file(url, output, resume=args.resume, threads=threads,
                              max_speed=args.max_speed, headers=headers, progress_position=0,
                              ignore_cache=args.no_cache, mode=args.mode, force=args.force,
                              skip_existing=args.skip_existing, retries=args.retry,
                              show_progress=show_progress)
                if args.json:
                    import json
                    print(json.dumps([{"url": url, "output": output, "status": "success"}], indent=2))
            except Exception as e:
                if args.json:
                    import json
                    print(json.dumps([{"url": url, "output": output, "status": "error", "message": str(e)}], indent=2))
                    sys.exit(1)
                raise
    except KeyboardInterrupt:
        logging.info("\nDownload interrupted by user.")
        sys.exit(1)
    except Exception as e:
        if args.json:
            import json
            print(json.dumps([{"status": "error", "message": str(e)}], indent=2))
            sys.exit(1)
        logging.error(f"easyget error: {e}")
        sys.exit(1)
