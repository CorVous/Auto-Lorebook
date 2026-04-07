"""Source metadata model for ingested lore files.

Every piece of ingested content carries a :class:`SourceMetadata`
descriptor so that citations can be generated later.  For YouTube SRTs,
:func:`youtube_timestamp_url` turns a start-time into a clickable
``&t=`` deep-link.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class SourceType(Enum):
    """The origin type of an ingested lore source."""

    youtube = auto()
    url = auto()
    local = auto()


@dataclass(frozen=True)
class SourceMetadata:
    """Describes the origin of an ingested lore file.

    :param url: Remote URL for the source, or *None* for local files.
    :param source_type: Enum member indicating the kind of source.
    :param filename: Original filename (always present; may be a stem
        derived from the URL for remote sources).
    """

    url: str | None
    source_type: SourceType
    filename: str | None


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be"}


def _is_youtube(url: str) -> bool:
    """Return *True* if *url* points to a YouTube domain."""
    # Avoid importing urllib.parse at module level for speed; it's stdlib.
    from urllib.parse import urlparse  # noqa: PLC0415

    host = urlparse(url).netloc.lower()
    return host in _YOUTUBE_HOSTS


def youtube_timestamp_url(url: str, seconds: float) -> str:
    """Append a ``&t=<seconds>`` parameter to a YouTube URL.

    :param url: Base YouTube watch URL (e.g. ``https://youtube.com/watch?v=ID``).
    :param seconds: Timestamp in seconds; fractional part is truncated.
    :return: URL with ``&t=<int>`` appended.
    :raises ValueError: If *url* is not a recognised YouTube URL.
    """
    if not _is_youtube(url):
        msg = f"Not a YouTube URL: {url!r}"
        raise ValueError(msg)
    t = int(seconds)
    return f"{url}&t={t}"


def make_source(*, url: str | None, filename: str | None) -> SourceMetadata:
    """Create a :class:`SourceMetadata` from a URL and/or filename.

    The :attr:`~SourceMetadata.source_type` is inferred automatically:

    * If *url* matches a YouTube domain → :attr:`SourceType.youtube`
    * If *url* is any other HTTP/S address → :attr:`SourceType.url`
    * If *url* is *None* → :attr:`SourceType.local`

    :param url: Optional remote URL for this source.
    :param filename: Original filename of the ingested file.
    :return: Populated :class:`SourceMetadata` instance.
    """
    if url is None:
        source_type = SourceType.local
    elif _is_youtube(url):
        source_type = SourceType.youtube
    else:
        source_type = SourceType.url

    return SourceMetadata(url=url, source_type=source_type, filename=filename)
