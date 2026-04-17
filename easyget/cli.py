import argparse
import sys
import logging
import base64
import os
from typing import Dict, List, Tuple

from .logging_utils import setup_logging
from .utils import get_filename_from_url, ProgressBar
from .input_parser import parse_file_list
from .wildcard import expand_wildcard_url
from .downloader import download_file

DEFAULT_THREADS = 4

def parse_args():
    parser = argparse.ArgumentParser(description="easyget: wget/curl compatible file downloader (Python 3.7+ Zero-dependency)")
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

    return parser.parse_args()

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

    try:
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
