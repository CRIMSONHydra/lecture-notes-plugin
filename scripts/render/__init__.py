"""render: markdown notes -> print-quality PDFs (WP2 of the `notes` plugin).

Modules
-------
layout     page geometry constants shared by the renderer and the audit
css        print CSS for the Basic and Advanced tiers (profile-branded, cross-OS font stacks)
markdown   markdown -> HTML: numbering, fences, math, mermaid, figures, callouts, links, meta block
assets     KaTeX / Mermaid downloaded once into <data>/cache/assets, sha256-pinned, inlined
cdp        headless Chrome over the DevTools protocol: print, JS-ready poll, error probe
paginate   page resolution, contents substitution, PDF outline and link fix-up

``scripts/build_pdf.py`` and ``scripts/audit_pdf.py`` are the entry points.
"""

__version__ = "0.1.0"
