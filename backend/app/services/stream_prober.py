import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger("dptv.stream_prober")


@dataclass
class ProbeResult:
    status: str
    """One of ProbeStatus's values: ok | timeout | error | unreachable | no_video_stream | no_url."""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate_kbps: int | None = None
    error: str | None = None


def _parse_frame_rate(value: str | None) -> float | None:
    if not value:
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        return round(num_f / den_f, 2) if den_f else None
    try:
        return round(float(value), 2)
    except ValueError:
        return None


async def probe_stream(url: str, timeout_seconds: float = 8.0, ffprobe_path: str = "ffprobe") -> ProbeResult:
    """Probes one stream URL's actual video resolution/framerate/bitrate via ffprobe.

    Live TS streams carry no container-level duration/bitrate metadata to just read instantly -
    ffprobe genuinely has to buffer a slice of the stream, so a bounded probesize/analyzeduration
    and an outer asyncio timeout (which kills the process) both matter here: without them a dead
    or slow stream would hang a scan indefinitely.
    """
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-of", "json",
        "-probesize", "5000000",
        "-analyzeduration", "5000000",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,bit_rate",
        "-show_entries", "format=bit_rate",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        logger.error("ffprobe binary not found (looked for %r)", ffprobe_path)
        return ProbeResult(status="error", error="ffprobe not found on server - install the ffmpeg package")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ProbeResult(status="timeout", error=f"No response within {timeout_seconds:.0f}s")

    if proc.returncode != 0:
        message = stderr.decode(errors="replace").strip()[:300] or "ffprobe failed"
        return ProbeResult(status="unreachable", error=message)

    try:
        data = json.loads(stdout or b"{}")
    except json.JSONDecodeError:
        return ProbeResult(status="error", error="Could not parse ffprobe output")

    streams = data.get("streams") or []
    if not streams:
        return ProbeResult(status="no_video_stream", error="No video stream detected")

    stream = streams[0]
    width = stream.get("width")
    height = stream.get("height")
    fps = _parse_frame_rate(stream.get("avg_frame_rate")) or _parse_frame_rate(stream.get("r_frame_rate"))
    bitrate = stream.get("bit_rate") or (data.get("format") or {}).get("bit_rate")
    bitrate_kbps = int(int(bitrate) / 1000) if bitrate else None

    return ProbeResult(status="ok", width=width, height=height, fps=fps, bitrate_kbps=bitrate_kbps)
