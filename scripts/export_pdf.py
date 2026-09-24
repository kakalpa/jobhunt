#!/usr/bin/env python3
"""
export_pdf.py - High-Quality Markdown to Recruiter-Ready PDF Converter
Uses uv, python markdown, and headless Chromium to produce clean, ATS-compliant A4 PDFs.
"""

import sys
import os
import re
import subprocess
import tempfile
import argparse

CSS_TEMPLATE = """
@page {
    size: A4;
    margin: 14mm 16mm 14mm 16mm;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 9.8pt;
    line-height: 1.45;
    color: #1e293b;
    background-color: #ffffff;
    margin: 0;
    padding: 0;
}

h1 {
    font-size: 19pt;
    font-weight: 800;
    color: #0f172a;
    margin-top: 0;
    margin-bottom: 3px;
    letter-spacing: -0.3px;
    text-transform: uppercase;
}

h2 {
    font-size: 11pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 1.5px solid #0284c7;
    padding-bottom: 2px;
    margin-top: 13px;
    margin-bottom: 7px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    page-break-after: avoid;
}

h3 {
    font-size: 10pt;
    font-weight: 600;
    color: #334155;
    margin-top: 8px;
    margin-bottom: 3px;
    page-break-after: avoid;
}

p {
    margin-top: 0;
    margin-bottom: 6px;
    text-align: justify;
}

ul {
    margin-top: 4px;
    margin-bottom: 8px;
    padding-left: 20px;
}

li {
    margin-bottom: 4px;
    page-break-inside: avoid;
}

li p {
    margin: 0;
    display: inline;
}

strong {
    color: #0f172a;
    font-weight: 600;
}

em {
    color: #475569;
    font-style: italic;
}

hr {
    border: 0;
    border-top: 1px solid #cbd5e1;
    margin: 10px 0;
}

a {
    color: #0284c7;
    text-decoration: none;
}
"""

def convert_md_to_html(md_text: str) -> str:
    import shutil
    cleaned_md = re.sub(r'([^\n])\n([ \t]*[-*+] )', r'\1\n\n\2', md_text)
    try:
        import markdown
        html_body = markdown.markdown(
            cleaned_md,
            extensions=["tables", "fenced_code", "sane_lists", "def_list"]
        )
    except ImportError:
        # Fallback via uv with markdown
        uv_bin = os.environ.get("UV_PATH") or shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        res = subprocess.run(
            [uv_bin, "run", "--python", "3.12", "--with", "markdown", "python3", "-c",
             "import sys, markdown; print(markdown.markdown(sys.stdin.read(), extensions=['tables', 'fenced_code', 'sane_lists']))"],
            input=cleaned_md,
            text=True,
            capture_output=True
        )
        if res.returncode == 0 and res.stdout.strip():
            html_body = res.stdout
        else:
            html_body = f"<pre style='font-family:inherit; white-space:pre-wrap;'>{md_text}</pre>"
    
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
{CSS_TEMPLATE}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
    return full_html

def find_chromium_executable() -> str:
    """Locate Chromium or Chrome executable across host OS and Docker containers."""
    import shutil
    env_chrome = os.environ.get("CHROMIUM_PATH")
    if env_chrome and os.path.exists(env_chrome):
        return env_chrome
    candidates = [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome"
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "chromium"

def generate_pdf(input_md_path: str, output_pdf_path: str = None) -> str:
    input_md_path = os.path.abspath(input_md_path)
    if not os.path.exists(input_md_path):
        raise FileNotFoundError(f"Input file not found: {input_md_path}")
        
    if output_pdf_path is None:
        base, _ = os.path.splitext(input_md_path)
        output_pdf_path = f"{base}.pdf"
    else:
        output_pdf_path = os.path.abspath(output_pdf_path)

    with open(input_md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    html_content = convert_md_to_html(md_text)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp_html:
        tmp_html.write(html_content)
        tmp_html_path = tmp_html.name

    chromium_bin = find_chromium_executable()
    try:
        cmd = [
            chromium_bin,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-pdf-header-footer",
            f"--print-to-pdf={output_pdf_path}",
            tmp_html_path
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=25)
        print(f"✅ Generated PDF: {output_pdf_path}")
        return output_pdf_path
    finally:
        if os.path.exists(tmp_html_path):
            os.remove(tmp_html_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Markdown CV/Cover Letter to clean A4 PDF.")
    parser.add_argument("input", help="Path to markdown file (.md)")
    parser.add_argument("-o", "--output", help="Optional output PDF path", default=None)
    args = parser.parse_args()

    generate_pdf(args.input, args.output)
