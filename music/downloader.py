import logging
import os
import re
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^\w\-. ]")


def _safe_filename(name: str) -> str:
    name = _SAFE_RE.sub("_", name)
    return name[:200]


async def download_attachment(url: str, filename: str, dest_dir: str) -> Optional[str]:
    """Download a URL to dest_dir/filename. Returns the saved path, or None on failure."""
    os.makedirs(dest_dir, exist_ok=True)
    safe = _safe_filename(filename)
    dest = os.path.join(dest_dir, safe)
    if os.path.exists(dest):
        return dest
    tmp = dest + ".part"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        f.write(chunk)
        os.replace(tmp, dest)
        logger.info("attachment_saved path=%r", dest)
        return dest
    except Exception as exc:
        logger.warning("attachment_download_failed url=%r error=%s", url, exc)
        if os.path.exists(tmp):
            os.remove(tmp)
        return None
