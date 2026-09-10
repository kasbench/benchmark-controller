"""Container image provenance for the KASBench Controller.

A running container cannot determine the name, tags, or ID of the image it was
launched from without access to the Docker daemon (the socket or API), because
that metadata lives in the daemon rather than inside the container filesystem.

To keep the controller self-contained and avoid mounting the Docker socket, the
image identity is injected as environment variables at ``docker run`` time and
read here. The host is responsible for populating these variables (for example
via ``docker inspect``); see the README "Image provenance" section.

Environment variables:
    KASBENCH_IMAGE_NAME  Repository/name of the image, e.g. ``kasbench/kasbench-controller``.
    KASBENCH_IMAGE_TAGS  Comma-separated tags, e.g. ``latest,v1.0``.
    KASBENCH_IMAGE_ID    Image ID (content digest), e.g. ``sha256:abc123...``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ENV_IMAGE_NAME = "KASBENCH_IMAGE_NAME"
ENV_IMAGE_TAGS = "KASBENCH_IMAGE_TAGS"
ENV_IMAGE_ID = "KASBENCH_IMAGE_ID"


@dataclass(frozen=True)
class ContainerImageInfo:
    """Identity of the Docker image the controller is running in.

    All fields are optional. When the controller runs outside a container (or
    the host did not inject the variables), ``name`` and ``image_id`` are ``None``
    and ``tags`` is an empty tuple. Use :meth:`is_available` to check whether any
    provenance was supplied.
    """

    name: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    image_id: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "ContainerImageInfo":
        """Build a :class:`ContainerImageInfo` from environment variables.

        Args:
            environ: Optional mapping to read from. Defaults to ``os.environ``.

        Returns:
            A populated :class:`ContainerImageInfo`. Missing or blank variables
            yield ``None``/empty values rather than raising, so this is safe to
            call whether or not the controller runs inside a container.
        """
        env = os.environ if environ is None else environ

        name = _clean(env.get(ENV_IMAGE_NAME))
        image_id = _clean(env.get(ENV_IMAGE_ID))

        raw_tags = env.get(ENV_IMAGE_TAGS) or ""
        tags = tuple(t.strip() for t in raw_tags.split(",") if t.strip())

        return cls(name=name, tags=tags, image_id=image_id)

    def is_available(self) -> bool:
        """Return True if any image provenance was supplied by the host."""
        return bool(self.name or self.tags or self.image_id)

    def as_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for structured logging."""
        return {
            "image_name": self.name,
            "image_tags": list(self.tags),
            "image_id": self.image_id,
        }


def _clean(value: str | None) -> str | None:
    """Normalize an env var: strip whitespace, treat blank as absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
