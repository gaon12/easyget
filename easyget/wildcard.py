import os
import re
import fnmatch
import logging
import urllib.request
from typing import List, Tuple
from urllib.parse import urlparse, urljoin
from .utils import get_filename_from_url

from .session import Session

def expand_wildcard_url(url: str, headers: dict) -> List[Tuple[str, str]]:
    """
    Expand a URL containing an asterisk (*) by listing the directory and matching the pattern.
    Supports basic HTML index pages.
    """
    parsed = urlparse(url)
    base_path = os.path.dirname(parsed.path)
    pattern = os.path.basename(parsed.path)
    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}/"
    
    session = Session()
    try:
        response = session.get(base_url, headers=headers)
        if response.status_code != 200:
            logging.error(f"easyget error: Directory listing failed (Status: {response.status_code})")
            return []
        
        content = response.text
        
        # Improved regex to handle both single and double quotes
        links = re.findall(r'href=["\']([^"\']+)["\']', content)
            
            matched_links = []
            seen_urls = set()
            for link in links:
                # Clean up the link (ignore fragments/params for matching)
                link_path = urlparse(link).path
                link_name = os.path.basename(link_path)
                
                if fnmatch.fnmatch(link_name, pattern):
                    full_url = urljoin(base_url, link)
                    if full_url not in seen_urls:
                        filename = os.path.basename(urlparse(full_url).path) or get_filename_from_url(full_url)
                        matched_links.append((full_url, filename))
                        seen_urls.add(full_url)
            
            if not matched_links:
                logging.error(f"easyget error: No files matching '{pattern}' at {base_url}")
                
            return matched_links
            
    except Exception as e:
        logging.error(f"easyget error: Wildcard expansion failed: {e}")
        return []
