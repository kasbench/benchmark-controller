"""Runner API client for communicating with the KASBench Runner HTTP API."""

from __future__ import annotations

import httpx
import structlog

from kasbench_controller.exceptions import RunnerAPIError

logger = structlog.get_logger()


class RunnerAPIClient:
    """HTTP client for the KASBench Runner API (port 8080).

    Wraps httpx.Client with structured error handling. Each method
    corresponds to a Runner API endpoint used during the benchmark lifecycle.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        """Initialize the Runner API client.

        Args:
            base_url: Base URL of the runner API (e.g. http://1.2.3.4:8080).
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def health_check(self) -> bool:
        """Check if the Runner API is available.

        GET /status — returns True if HTTP 200, False otherwise.
        Does not raise on non-200 responses.
        """
        try:
            response = self._client.get("/status")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def initialize(self, config: dict) -> httpx.Response:
        """Send initialization configuration to the Runner.

        POST /initialize with the provided config body.

        Args:
            config: Dictionary containing initialization parameters.

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/initialize"
        response = self._request("POST", endpoint, json=config)
        return response

    def rollout_wait(
        self, deployment_name: str, namespace: str, timeout: int
    ) -> httpx.Response:
        """Wait for a Kubernetes deployment rollout to complete.

        POST /rollout/wait with deployment details.

        Args:
            deployment_name: Name of the Kubernetes deployment.
            namespace: Kubernetes namespace.
            timeout: Rollout timeout in seconds.

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/rollout/wait"
        body = {
            "deploymentName": deployment_name,
            "namespace": namespace,
            "timeout": timeout,
        }
        response = self._request("POST", endpoint, json=body)
        return response

    def snapshot(self, phase: str) -> httpx.Response:
        """Take a cluster snapshot.

        POST /snapshot with the phase identifier.

        Args:
            phase: Snapshot phase (e.g. "pre" or "post").

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/snapshot"
        response = self._request("POST", endpoint, json={"phase": phase})
        return response

    def start(self) -> httpx.Response:
        """Start the benchmark load generation.

        POST /start with empty JSON body.

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/start"
        response = self._request("POST", endpoint, json={})
        return response

    def status(self) -> httpx.Response:
        """Get the current benchmark status.

        GET /status — returns the full response for status parsing.

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/status"
        response = self._request("GET", endpoint)
        return response

    def shutdown(self) -> httpx.Response:
        """Shut down the Runner.

        POST /shutdown.

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = "/shutdown"
        response = self._request("POST", endpoint)
        return response

    def export(self, export_type: str) -> httpx.Response:
        """Trigger a data export.

        POST /{export_type}/export for metrics, metadata, tsdb, output, db.

        Args:
            export_type: Type of export (metrics, metadata, tsdb, output, db).

        Returns:
            The HTTP response from the runner.

        Raises:
            RunnerAPIError: If the response status code is not successful.
        """
        endpoint = f"/{export_type}/export"
        response = self._request("POST", endpoint)
        return response

    def _request(
        self,
        method: str,
        endpoint: str,
        json: dict | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request and raise on non-successful status.

        Args:
            method: HTTP method (GET, POST).
            endpoint: API endpoint path.
            json: Optional JSON body.

        Returns:
            The HTTP response.

        Raises:
            RunnerAPIError: If the response status code indicates failure.
        """
        try:
            response = self._client.request(method, endpoint, json=json)
        except httpx.HTTPError as exc:
            raise RunnerAPIError(
                message=f"HTTP request failed for {endpoint}: {exc}",
                endpoint=endpoint,
            ) from exc

        if not response.is_success:
            raise RunnerAPIError(
                message=(
                    f"Runner API request to {endpoint} failed with "
                    f"status {response.status_code}: {response.text}"
                ),
                endpoint=endpoint,
                status_code=response.status_code,
                response_body=response.text,
            )

        return response
