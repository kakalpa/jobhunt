---
name: pdf-cv-exporter
description: Convert tailored markdown CVs and cover letters into clean, ATS-compliant, recruiter-ready A4 PDFs using headless Chromium
---

# PDF CV Exporter

## When to Use This Skill

Activate this skill when:
- Tailoring of a CV or cover letter is complete and the user needs a recruiter-ready, uploadable document
- Preparing application packages for submission to online job portals (Workday, Teamtailor, Kuntarekry, Lever, Greenhouse, etc.)
- Converting any `.md` CV, resume, or cover letter to a PDF

---

## Execution Command

This skill leverages `scripts/export_pdf.py` which uses `uv` and headless Chromium to produce an A4, single/two-page, vector-text PDF.

```bash
uv run --with markdown python3 scripts/export_pdf.py <path-to-markdown-file.md>
```

### Example:
```bash
uv run --with markdown python3 scripts/export_pdf.py ExampleCompany_Role/Candidate_ExampleCompany_Role.md
uv run --with markdown python3 scripts/export_pdf.py ExampleCompany_Role/Candidate_Cover_Letter_ExampleCompany_Role.md
```

---

## ATS Formatting Guarantees

1. **Standard Page Layout:** A4 dimensions with 14mm top/bottom and 16mm side margins.
2. **Text Selectability:** 100% vector-rendered text; parseable by all legacy and modern ATS OCR engines.
3. **No Header/Footer Clutter:** Strips default browser print headers and footers (timestamps, URLs, page numbers).
4. **List Hierarchy:** Accurately converts markdown bullet points into distinct `<ul><li>` items without text mergers.
5. **Page-Break Protection:** Prevents headers and individual bullet points from splitting awkwardly across page boundaries.
