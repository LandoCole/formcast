"""The conftest static server serves files from the repository root."""

from __future__ import annotations


def test_server_serves_vendor_readme(server, page):
    response = page.goto(f"{server}/demo/web/vendor/README.md")
    assert response is not None
    assert response.status == 200
    assert "pdfjs-dist" in page.content()


def test_server_sends_module_and_wasm_types(server, page):
    for path, content_type in [
        ("/demo/web/core/geometry.js", "text/javascript"),
        ("/demo/web/vendor/pdfjs/build/pdf.min.mjs", "text/javascript"),
        ("/demo/web/vendor/onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm", "application/wasm"),
    ]:
        response = page.request.head(f"{server}{path}")
        assert response.status == 200, path
        assert response.headers["content-type"].startswith(content_type), path
