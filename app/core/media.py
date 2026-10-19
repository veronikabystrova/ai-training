"""Public URLs for stored media keys (avatars, logos)."""


class MediaUrlBuilder:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    def url(self, key: str | None) -> str | None:
        return None if key is None else f"{self._base}/{key}"
