import asyncio
import os
from typing import Optional
from music.track import Track

def resolve_local_track(
    filename: str, requester: str, music_dir: Optional[str]
) -> Optional[Track]:
    if music_dir is None:
        return None
    # Resolve and check for path traversal
    full_path = os.path.normpath(os.path.join(music_dir, filename))
    if not full_path.startswith(os.path.normpath(music_dir) + os.sep):
        return None
    if not os.path.isfile(full_path):
        return None
    return Track(
        title=os.path.basename(filename),
        url=full_path,
        source="local",
        duration=None,
        requester=requester,
        stream_url=full_path,
    )

async def probe_duration(path: str) -> Optional[int]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return int(float(stdout.decode().strip()))
    except (ValueError, asyncio.TimeoutError, FileNotFoundError):
        return None
