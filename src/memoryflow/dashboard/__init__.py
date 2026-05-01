"""Static dashboard helpers.

The dashboard layer consumes report dictionaries only; it does not import or
drive the verifier.
"""

from memoryflow.reports import render_html_report

__all__ = ["render_html_report"]
