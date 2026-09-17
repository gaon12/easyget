import fnmatch
import logging
import posixpath
import re
from urllib.parse import urljoin, urlparse

from .session import Session
from .utils import get_filename_from_url

logger = logging.getLogger(__name__)


def expand_wildcard_url(url: str, headers: dict) -> list[tuple[str, str]]:
    """
    Expand a URL containing an asterisk (*) by listing the directory and matching the pattern.
    Supports basic HTML index pages.
    """
    parsed = urlparse(url)
    # URL paths always use '/', so use posixpath (os.path is '\' on Windows).
    base_path = posixpath.dirname(parsed.path)
    pattern = posixpath.basename(parsed.path)
    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}/"

    session = Session()
    try:
        response = session.get(base_url, headers=headers)
        if response.status_code != 200:
            logger.error(
                f"easyget error: Directory listing failed (Status: {response.status_code})"
            )
            return []

        content = response.text

        # Improved regex to handle both single and double quotes
        links = re.findall(r'href=["\']([^"\']+)["\']', content)

        matched_links = []
        seen_urls = set()
        for link in links:
            # Clean up the link (ignore fragments/params for matching)
            link_path = urlparse(link).path
            link_name = posixpath.basename(link_path)

            if fnmatch.fnmatch(link_name, pattern):
                full_url = urljoin(base_url, link)
                if full_url not in seen_urls:
                    filename = posixpath.basename(
                        urlparse(full_url).path
                    ) or get_filename_from_url(full_url)
                    matched_links.append((full_url, filename))
                    seen_urls.add(full_url)

        if not matched_links:
            logger.error(f"easyget error: No files matching '{pattern}' at {base_url}")

        return matched_links

    except Exception as e:
        logger.error(f"easyget error: Wildcard expansion failed: {e}")
        return []
    finally:
        session.close()
