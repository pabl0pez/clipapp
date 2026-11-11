"""Exceptions raised by the media package."""


class MediaError(Exception):
    """Base class for media errors.

    Args:
        message: Human-readable description.
        stderr_tail: Last lines of the tool's stderr, when a tool was involved.
    """

    def __init__(self, message: str, stderr_tail: str = "") -> None:
        """Store the message and the optional stderr tail."""
        self.stderr_tail = stderr_tail
        super().__init__(f"{message}\n{stderr_tail}" if stderr_tail else message)


class MediaNotFoundError(MediaError):
    """The media file does not exist."""


class NoVideoStreamError(MediaError):
    """The file has no video stream."""


class NoAudioStreamError(MediaError):
    """The file has no audio stream."""


class ProbeError(MediaError):
    """ffprobe failed or returned unusable output."""


class FFmpegError(MediaError):
    """ffmpeg exited with a non-zero code."""


class FFmpegCancelledError(FFmpegError):
    """The ffmpeg run was cancelled through its cancel event."""
