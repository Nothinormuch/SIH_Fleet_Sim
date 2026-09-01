"""Converts all markdown files in docs/ to standalone, browser-compatible HTML files.

Includes:
- Responsive, modern styling (dark/light theme with CSS variables)
- High-contrast, clean table styling with alternating rows & hover effects
- MathJax CDN integration for LaTeX formulas (with raw text fallback)
- Offline-friendly embedded CSS
- Generates a docs/index.html hub linking all documentation
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} - SIH Fleet Sim Docs</title>
  <!-- MathJax for rendering formulas like O(N log N) -->
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }},
      svg: {{ fontCache: 'global' }}
    }};
  </script>
  <script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
  <style>
    :root {{
      --bg-color: #0d1117;
      --card-bg: #161b22;
      --card-border: #30363d;
      --text-main: #c9d1d9;
      --text-muted: #8b949e;
      --text-heading: #f0f6fc;
      --primary: #58a6ff;
      --primary-hover: #79c0ff;
      --accent: #238636;
      --table-header: #21262d;
      --table-border: #30363d;
      --table-alt: #161b22;
      --table-hover: #1c2128;
      --code-bg: #1f242c;
      --code-border: #30363d;
      --tag-bg: #388bfd26;
      --tag-color: #58a6ff;
    }}

    @media (prefers-color-scheme: light) {{
      :root {{
        --bg-color: #f6f8fa;
        --card-bg: #ffffff;
        --card-border: #d0d7de;
        --text-main: #24292f;
        --text-muted: #57606a;
        --text-heading: #1f2328;
        --primary: #0969da;
        --primary-hover: #0550ae;
        --accent: #1a7f37;
        --table-header: #f6f8fa;
        --table-border: #d0d7de;
        --table-alt: #ffffff;
        --table-hover: #f3f4f6;
        --code-bg: #eff1f3;
        --code-border: #d0d7de;
        --tag-bg: #ddf4ff;
        --tag-color: #0969da;
      }}
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.65;
      background-color: var(--bg-color);
      color: var(--text-main);
      padding: 24px 16px;
    }}

    .container {{
      max-width: 1080px;
      margin: 0 auto;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 40px 48px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
    }}

    .nav-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      margin-bottom: 28px;
      border-bottom: 1px solid var(--card-border);
    }}

    .nav-bar a {{
      color: var(--primary);
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .nav-bar a:hover {{
      color: var(--primary-hover);
      text-decoration: underline;
    }}

    .badge {{
      display: inline-block;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 20px;
      background: var(--tag-bg);
      color: var(--tag-color);
    }}

    h1, h2, h3, h4, h5, h6 {{
      color: var(--text-heading);
      margin-top: 32px;
      margin-bottom: 14px;
      font-weight: 600;
      line-height: 1.3;
    }}

    h1 {{
      font-size: 28px;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 12px;
      margin-top: 12px;
    }}

    h2 {{
      font-size: 22px;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 8px;
    }}

    h3 {{ font-size: 18px; }}
    h4 {{ font-size: 16px; }}

    p {{
      margin-bottom: 16px;
    }}

    a {{
      color: var(--primary);
      text-decoration: none;
    }}

    a:hover {{
      text-decoration: underline;
    }}

    /* Table styles */
    .table-wrapper {{
      overflow-x: auto;
      margin: 20px 0;
      border-radius: 8px;
      border: 1px solid var(--table-border);
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }}

    th, td {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--table-border);
    }}

    th {{
      background-color: var(--table-header);
      color: var(--text-heading);
      font-weight: 600;
      white-space: nowrap;
    }}

    tr:nth-child(even) {{
      background-color: var(--table-alt);
    }}

    tr:hover {{
      background-color: var(--table-hover);
    }}

    /* Code blocks */
    pre {{
      background-color: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: 8px;
      padding: 16px;
      overflow-x: auto;
      margin: 18px 0;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      line-height: 1.5;
    }}

    code {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      background: var(--code-bg);
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid var(--code-border);
    }}

    pre code {{
      background: none;
      padding: 0;
      border: none;
    }}

    /* Lists */
    ul, ol {{
      margin-bottom: 16px;
      padding-left: 28px;
    }}

    li {{
      margin-bottom: 6px;
    }}

    /* Blockquotes */
    blockquote {{
      border-left: 4px solid var(--primary);
      padding: 8px 18px;
      background: var(--table-alt);
      color: var(--text-muted);
      margin: 16px 0;
      border-radius: 0 8px 8px 0;
    }}

    hr {{
      height: 1px;
      background: var(--card-border);
      border: none;
      margin: 32px 0;
    }}

    footer {{
      margin-top: 40px;
      padding-top: 16px;
      border-top: 1px solid var(--card-border);
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="nav-bar">
      <a href="index.html">← Back to Documentation Hub</a>
      <span class="badge">SIH Fleet Sim 2026</span>
    </div>
    <article>
{content}
    </article>
    <footer>
      <span>Generated for SIH Fleet Sim AMR Platform</span>
      <a href="index.html">All Documentation</a>
    </footer>
  </div>
</body>
</html>
"""


def markdown_to_html(md_text: str) -> str:
    """Lightweight, robust Markdown to HTML parser supporting tables, code, math, and lists."""
    lines = md_text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_table = False
    table_rows: list[list[str]] = []
    in_list = False
    list_type = "ul"

    def flush_table():
        nonlocal in_table, table_rows
        if not in_table or not table_rows:
            return
        html_tbl = ['<div class="table-wrapper"><table>']
        # Header
        if table_rows:
            html_tbl.append("  <thead><tr>")
            for cell in table_rows[0]:
                html_tbl.append(f"    <th>{format_inline(cell.strip())}</th>")
            html_tbl.append("  </tr></thead>")
        # Body
        if len(table_rows) > 1:
            html_tbl.append("  <tbody>")
            for row in table_rows[1:]:
                html_tbl.append("    <tr>")
                for cell in row:
                    html_tbl.append(f"      <td>{format_inline(cell.strip())}</td>")
                html_tbl.append("    </tr>")
            html_tbl.append("  </tbody>")
        html_tbl.append("</table></div>")
        out.append("\n".join(html_tbl))
        in_table = False
        table_rows = []

    def flush_list():
        nonlocal in_list, list_type
        if in_list:
            out.append(f"</{list_type}>")
            in_list = False

    def format_inline(text: str) -> str:
        # Protect math expressions $...$ from HTML escaping
        math_tokens = []
        def save_math(m):
            math_tokens.append(m.group(0))
            return f"__MATH_TOKEN_{len(math_tokens)-1}__"
        
        text = re.sub(r"\$([^\$\n]+)\$", save_math, text)

        # Inline code `code`
        text = re.sub(r"`([^`]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", text)

        # Links [text](url) -> if target is .md, update to .html
        def repl_link(m):
            t, url = m.group(1), m.group(2)
            if url.endswith(".md"):
                url = url[:-3] + ".html"
            return f'<a href="{html.escape(url)}">{html.escape(t)}</a>'
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, text)

        # Bold & Italic
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)

        # Restore math
        for i, tok in enumerate(math_tokens):
            text = text.replace(f"__MATH_TOKEN_{i}__", tok)

        return text

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code fence
        if line.startswith("```"):
            if in_code:
                in_code = False
                escaped_code = html.escape("\n".join(code_lines))
                out.append(f'<pre><code class="language-{code_lang}">{escaped_code}</code></pre>')
                code_lines = []
            else:
                flush_table()
                flush_list()
                in_code = True
                code_lang = line[3:].strip()
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^\s*(\-{3,}|\*{3,}|_{3,})\s*$", line):
            flush_table()
            flush_list()
            out.append("<hr>")
            i += 1
            continue

        # Headings
        m_head = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m_head:
            flush_table()
            flush_list()
            lvl = len(m_head.group(1))
            h_text = format_inline(m_head.group(2))
            out.append(f"<h{lvl}>{h_text}</h{lvl}>")
            i += 1
            continue

        # Tables
        if "|" in line and line.strip().startswith("|") and line.strip().endswith("|"):
            flush_list()
            cells = [c for c in line.strip().split("|")[1:-1]]
            # Check if this is divider row
            if all(re.match(r"^[\s\-:]+$", c) for c in cells):
                # Divider row; continue
                i += 1
                continue
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(cells)
            i += 1
            continue
        else:
            flush_table()

        # Blockquote
        if line.startswith(">"):
            flush_list()
            bq_text = format_inline(line[1:].strip())
            out.append(f"<blockquote><p>{bq_text}</p></blockquote>")
            i += 1
            continue

        # Unordered list
        m_ul = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m_ul:
            if not in_list or list_type != "ul":
                flush_list()
                in_list = True
                list_type = "ul"
                out.append("<ul>")
            out.append(f"  <li>{format_inline(m_ul.group(1))}</li>")
            i += 1
            continue

        # Ordered list
        m_ol = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m_ol:
            if not in_list or list_type != "ol":
                flush_list()
                in_list = True
                list_type = "ol"
                out.append("<ol>")
            out.append(f"  <li>{format_inline(m_ol.group(1))}</li>")
            i += 1
            continue

        flush_list()

        # Regular paragraph or blank line
        if line.strip():
            out.append(f"<p>{format_inline(line.strip())}</p>")

        i += 1

    flush_table()
    flush_list()
    return "\n".join(out)


def generate_index_page(doc_files: list[tuple[str, str, str]]) -> str:
    """Generates an index.html navigation hub listing all converted documents."""
    cards_html = []
    for filename, title, snippet in sorted(doc_files):
        cards_html.append(f"""
        <div class="doc-card">
          <div class="doc-card-header">
            <h3><a href="{filename}">{title}</a></h3>
            <span class="badge">DOC</span>
          </div>
          <p class="doc-snippet">{snippet}</p>
          <a class="doc-link" href="{filename}">Open Document →</a>
        </div>
        """)

    content = f"""
    <h1>SIH Fleet Sim — Documentation Hub</h1>
    <p style="font-size: 16px; color: var(--text-muted); margin-bottom: 28px;">
      Interactive, browser-compatible technical specifications, algorithm benchmarks, and architecture papers.
    </p>
    <div class="doc-grid">
      {"".join(cards_html)}
    </div>
    """

    base_page = HTML_TEMPLATE.format(title="Documentation Hub", content=content)
    base_page = base_page.replace(
      '<a href="index.html">← Back to Documentation Hub</a>',
      '<span class="badge">Documentation Hub</span>'
    ).replace(
      '</style>',
      """
      .doc-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 20px;
        margin-top: 24px;
      }
      .doc-card {
        background: var(--table-alt);
        border: 1px solid var(--card-border);
        border-radius: 10px;
        padding: 20px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        transition: transform 0.15s ease, border-color 0.15s ease;
      }
      .doc-card:hover {
        transform: translateY(-2px);
        border-color: var(--primary);
      }
      .doc-card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 10px;
      }
      .doc-card-header h3 {
        margin: 0;
        font-size: 16px;
      }
      .doc-snippet {
        font-size: 13px;
        color: var(--text-muted);
        line-height: 1.5;
        margin-bottom: 16px;
        flex-grow: 1;
      }
      .doc-link {
        font-size: 13px;
        font-weight: 600;
        color: var(--primary);
      }
      </style>
      """
    )
    return base_page


def main() -> None:
    md_files = list(DOCS_DIR.glob("*.md"))
    print(f"Found {len(md_files)} markdown documents in {DOCS_DIR}")

    doc_catalog: list[tuple[str, str, str]] = []

    for md_path in md_files:
        raw_text = md_path.read_text(encoding="utf-8")
        
        # Extract title from first H1 or use filename
        m_title = re.search(r"^#\s+(.+)$", raw_text, re.MULTILINE)
        title = m_title.group(1).strip() if m_title else md_path.stem.replace("_", " ")

        # Extract short snippet (first non-heading paragraph)
        snippet = ""
        for line in raw_text.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("|") and not line.startswith("-"):
                snippet = line[:140] + ("..." if len(line) > 140 else "")
                break
        if not snippet:
            snippet = f"Technical documentation and specifications for {title}."

        # Convert to HTML
        html_body = markdown_to_html(raw_text)
        full_html = HTML_TEMPLATE.format(title=title, content=html_body)

        out_html_path = DOCS_DIR / f"{md_path.stem}.html"
        out_html_path.write_text(full_html, encoding="utf-8")
        print(f"  -> Generated: {out_html_path.name}")

        doc_catalog.append((out_html_path.name, title, snippet))

    # Generate index.html
    index_html = generate_index_page(doc_catalog)
    (DOCS_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\nSuccessfully generated {DOCS_DIR / 'index.html'} portal!")


if __name__ == "__main__":
    main()
