"""Tests for the web dashboard routes and markdown renderer."""

from __future__ import annotations

from supavision.web.dashboard import _inline, _md_to_html

# ── Markdown renderer ───────────────────────────────────────────


class TestInline:
    def test_bold(self):
        assert "<strong>bold</strong>" in _inline("**bold**")

    def test_code(self):
        assert "<code>code</code>" in _inline("`code`")

    def test_both(self):
        result = _inline("**bold** and `code`")
        assert "<strong>" in result
        assert "<code>" in result

    def test_no_markup(self):
        assert _inline("plain text") == "plain text"


class TestMdToHtml:
    def test_headers(self):
        html = _md_to_html("# H1\n## H2\n### H3")
        assert "<h2>" in html
        assert "<h3>" in html
        assert "<h4>" in html

    def test_bold_in_header(self):
        html = _md_to_html("## Status: **CRITICAL**")
        assert "<strong>CRITICAL</strong>" in html

    def test_code_in_list(self):
        html = _md_to_html("- Check `nginx` status")
        assert "<code>nginx</code>" in html

    def test_table(self):
        md = "| Name | Value |\n|---|---|\n| CPU | 50% |"
        html = _md_to_html(md)
        assert "<table" in html
        assert "<th>Name</th>" in html
        assert "<td>CPU</td>" in html

    def test_code_block(self):
        md = "```\nprint('hello')\n```"
        html = _md_to_html(md)
        assert "<pre" in html
        assert "print" in html

    def test_list_bold(self):
        html = _md_to_html("- **Disk**: 97%")
        assert "<strong>Disk</strong>" in html
        assert "<li>" in html

    def test_hr(self):
        assert "<hr>" in _md_to_html("---")

    def test_paragraph(self):
        html = _md_to_html("Hello world")
        assert "<p>Hello world</p>" in html

    def test_xss_escaped(self):
        html = _md_to_html("<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty_input(self):
        assert _md_to_html("") == ""

    def test_numbered_list(self):
        md = "1. First\n2. Second\n3. Third"
        html = _md_to_html(md)
        assert "<ol>" in html
        assert "</ol>" in html
        assert html.count("<li>") == 3

    def test_unordered_list_wrapping(self):
        md = "- Alpha\n- Beta"
        html = _md_to_html(md)
        assert "<ul>" in html
        assert "</ul>" in html

    def test_mixed_list_types(self):
        md = "- Bullet\n\n1. Number"
        html = _md_to_html(md)
        assert "<ul>" in html
        assert "</ul>" in html
        assert "<ol>" in html
        assert "</ol>" in html

    def test_list_closes_before_header(self):
        md = "- Item\n## Header"
        html = _md_to_html(md)
        assert html.index("</ul>") < html.index("<h3>")

    def test_italics(self):
        html = _md_to_html("This is *italic* text")
        assert "<em>italic</em>" in html

    def test_bold_and_italic_coexist(self):
        html = _md_to_html("**bold** and *italic*")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    def test_bold_in_table_cell(self):
        md = "| Status |\n|---|\n| **OK** |"
        html = _md_to_html(md)
        assert "<strong>OK</strong>" in html


# ── Resource type metadata ──────────────────────────────────────


class TestResourceTypes:
    def test_all_types_defined(self):
        from supavision.resource_types import RESOURCE_TYPES

        assert "server" in RESOURCE_TYPES
        assert "aws_account" in RESOURCE_TYPES
        assert "database" in RESOURCE_TYPES
        assert "github_org" in RESOURCE_TYPES

    def test_server_is_ssh(self):
        from supavision.resource_types import RESOURCE_TYPES

        assert RESOURCE_TYPES["server"]["connection"] == "ssh"

    def test_aws_is_local(self):
        from supavision.resource_types import RESOURCE_TYPES

        assert RESOURCE_TYPES["aws_account"]["connection"] == "credentials"

    def test_all_have_required_fields(self):
        from supavision.resource_types import RESOURCE_TYPES

        for slug, rt in RESOURCE_TYPES.items():
            assert "label" in rt, f"{slug} missing label"
            assert "description" in rt, f"{slug} missing description"
            assert "how_it_works" in rt, f"{slug} missing how_it_works"
            assert "connection" in rt, f"{slug} missing connection"
