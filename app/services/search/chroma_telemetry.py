"""Chroma telemetry adapter for backend local runtime."""

from chromadb.telemetry.product import ProductTelemetryClient
from chromadb.telemetry.product import ProductTelemetryEvent
from overrides import override


class NoOpTelemetryClient(ProductTelemetryClient):
    """Disable product telemetry emission to keep runtime logs clean."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:  # noqa: ARG002
        return None
