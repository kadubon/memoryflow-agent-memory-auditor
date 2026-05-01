from memoryflow.reports import render_html_report


def test_html_report_escapes_user_controlled_fields() -> None:
    html = render_html_report(
        {
            "status": "VALID",
            "profile": "P1",
            "metrics": [
                {
                    "name": "<script>alert(1)</script>",
                    "status": "VALID",
                    "value": {"num": "<img src=x onerror=alert(2)>", "den": "1"},
                }
            ],
            "diagnostics": [
                {
                    "severity": "WARNING",
                    "code": "XSS",
                    "line": 1,
                    "message": "<script>alert(3)</script>",
                }
            ],
        }
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
