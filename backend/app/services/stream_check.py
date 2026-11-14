"""Plays a short sample of a stream and says whether it really shows something (1.0.15).

A stream's own description (ffprobe) can claim 2160p at 50 fps while showing a black screen, a frozen
frame or a "channel unavailable" card. The sample is decoded by ffmpeg with three filters:
blackdetect (black picture), freezedetect (a picture that does not change: frozen video and static
cards) and silencedetect (no sound), and has to arrive at about real time.

The URL carries the provider's login: it is never logged.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# More than this share of the sample black, frozen or silent counts as black, frozen or silent.
BAD_SHARE = 0.5
# The sample has to deliver at least this share of its length (a stream that stalls doesn't).
MIN_DELIVERED = 0.8

_STREAM_VIDEO = re.compile(r"Stream #\S+.*?Video: .*?(\d{2,5})x(\d{2,5})(?:.*?([\d.]+) fps)?")
_STREAM_AUDIO = re.compile(r"Stream #\S+.*?Audio: ")
_BITRATE = re.compile(r"Duration: .*?bitrate: (\d+) kb/s")
_BLACK = re.compile(r"black_duration:\s*([\d.]+)")
_FREEZE = re.compile(r"freeze_duration:\s*([\d.]+)")
_SILENCE = re.compile(r"silence_duration:\s*([\d.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_TIME = re.compile(r"time=(\d+):(\d+):([\d.]+)")


@dataclass
class CheckResult:
    ok: bool
    reason: str
    height: int | None = None
    fps: float | None = None
    bitrate_kbps: int | None = None
    silent: bool | None = None


def _seconds(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def analyse(stderr: str, sample_seconds: float) -> CheckResult:
    """Reads what ffmpeg said about a sample of [sample_seconds]: picture size, then how much was
    delivered, black, frozen and silent. Ended-but-unclosed freeze/silence periods run to the end."""
    video = _STREAM_VIDEO.search(stderr)
    height = int(video.group(2)) if video else None
    fps = float(video.group(3)) if video and video.group(3) else None
    rate = _BITRATE.search(stderr)
    bitrate = int(rate.group(1)) if rate else None
    has_audio = bool(_STREAM_AUDIO.search(stderr))

    times = _TIME.findall(stderr)
    delivered = _seconds(*times[-1]) if times else 0.0

    def total(pattern: re.Pattern, open_pattern: re.Pattern | None) -> float:
        closed = sum(float(v) for v in pattern.findall(stderr))
        if open_pattern is not None:
            starts = open_pattern.findall(stderr)
            if len(starts) > len(pattern.findall(stderr)):
                # The last period was still going when the sample ended.
                closed += max(0.0, delivered - max(0.0, float(starts[-1])))
        return closed

    black = total(_BLACK, None)
    frozen = total(_FREEZE, _FREEZE_START)
    silence = total(_SILENCE, _SILENCE_START) if has_audio else delivered
    span = max(delivered, 0.001)
    silent = (not has_audio) or silence / span > BAD_SHARE
    found = dict(height=height, fps=fps, bitrate_kbps=bitrate, silent=silent)

    if not video:
        return CheckResult(False, "no picture in the stream", **found)
    if delivered < sample_seconds * MIN_DELIVERED:
        return CheckResult(False, f"stalled: {delivered:.0f} of {sample_seconds:.0f} s arrived", **found)
    if black / span > BAD_SHARE:
        return CheckResult(False, f"black for {black:.0f} of {delivered:.0f} s", **found)
    if frozen / span > BAD_SHARE:
        return CheckResult(False, f"frozen picture for {frozen:.0f} of {delivered:.0f} s", **found)
    label = f"{height}p" + (f" {fps:g} fps" if fps else "")
    return CheckResult(True, label + (", no sound" if silent else ""), **found)


def ffmpeg_args(url: str, sample_seconds: float, ffmpeg_path: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "info", "-stats_period", "1",
        "-rw_timeout", "10000000", "-i", url, "-t", str(sample_seconds),
        "-vf", "blackdetect=d=1:pix_th=0.10,freezedetect=n=0.003:d=4",
        "-af", "silencedetect=n=-50dB:d=4",
        "-f", "null", "-",
    ]


async def check_stream(url: str, sample_seconds: float = 20, ffmpeg_path: str = "ffmpeg") -> CheckResult:
    """Plays [sample_seconds] of [url] and judges it. Never raises: a failure is a failed check."""
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args(url, sample_seconds, ffmpeg_path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CheckResult(False, "ffmpeg is not installed on the server")
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=sample_seconds * 2 + 20)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CheckResult(False, "stalled: the stream stopped sending")
    result = analyse(err.decode("utf-8", errors="replace"), sample_seconds)
    logger.info("stream check: %s (%.0f s)", result.reason, time.monotonic() - started)
    return result
