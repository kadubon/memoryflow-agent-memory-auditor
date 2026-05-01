"""Optional HTTP server integration for MemoryFlow.

This package is not imported by the normative verifier. Install the `server`
extra to use it.
"""

from memoryflow.server.app import create_app

__all__ = ["create_app"]
