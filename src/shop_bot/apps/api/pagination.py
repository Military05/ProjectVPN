from __future__ import annotations

from fastapi import Response


def set_pagination_headers(
    response: Response,
    *,
    limit: int,
    offset: int,
    has_more: bool,
) -> None:
    response.headers["X-Page-Limit"] = str(limit)
    response.headers["X-Page-Offset"] = str(offset)
    response.headers["X-Has-More"] = str(has_more).lower()
