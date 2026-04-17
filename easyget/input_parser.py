import os
import csv
import logging
from typing import List, Tuple
from .utils import get_filename_from_url

def parse_file_list(file_path: str) -> List[Tuple[str, str]]:
    """
    Parse an input file (txt, csv, or tsv) to extract a list of (URL, filename) tuples.
    """
    file_list: List[Tuple[str, str]] = []
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        with open(file_path, encoding='utf-8') as f:
            if ext == '.txt':
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        file_list.append((line, get_filename_from_url(line)))
            elif ext in ['.csv', '.tsv']:
                delimiter = ',' if ext == '.csv' else '\t'
                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    url_val = row.get("url")
                    if not url_val:
                        # Fallback if no 'url' header exists, try first column
                        url_val = next(iter(row.values()))
                    
                    if not url_val:
                        continue
                        
                    filename_val = row.get("filename") or get_filename_from_url(url_val)
                    file_list.append((url_val.strip(), filename_val.strip()))
            else:
                logging.error(f"easyget error: Unsupported file format '{ext}'. Supported: txt, csv, tsv.")
    except Exception as e:
        logging.error(f"easyget error: Failed to parse file list '{file_path}': {e}")
        
    return file_list
