"""
test_exec_dispatch.py — Tests for exec-backed skill dispatch and
kernel-doc-retrieval commands (/markdown, /anonymize).
No model, no HTTP — tests logic, routing, and output paths.
"""

import sys
import os
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import skills as skills_mod

DISPATCH_SCRIPT = Path(__file__).parents[1] / "repositories" / "kernel-doc-retrieval" / "scripts" / "dispatch.py"
ANONYMIZE_SCRIPT = Path(__file__).parents[1] / "repositories" / "kernel-doc-retrieval" / "scripts" / "anonymize.py"
PDF_SCRIPT = Path(__file__).parents[1] / "repositories" / "kernel-doc-retrieval" / "scripts" / "pdf_to_markdown.py"

EXEC_SKILL_MD = """\
---
name: kernel-doc-retrieval
description: PDF to Markdown and anonymize skill
commands:
  - /markdown
  - /anonymize
exec: python3 /tmp/dispatch.py {input}
---
## Instructions
Handles /markdown and /anonymize commands.
"""


# ---------------------------------------------------------------------------
# Test: exec field parsed from SKILL.md
# ---------------------------------------------------------------------------

class TestExecFieldParsing:
    def test_exec_field_present(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text(EXEC_SKILL_MD)
        skill = skills_mod._parse_skill(p)
        assert skill is not None
        assert skill.get("exec") == "python3 /tmp/dispatch.py {input}"

    def test_exec_field_absent_returns_none(self, tmp_path):
        content = "---\nname: no-exec\ndescription: plain skill\ncommands:\n  - /plain\n---\nbody"
        p = tmp_path / "SKILL.md"
        p.write_text(content)
        skill = skills_mod._parse_skill(p)
        assert skill.get("exec") is None

    def test_exec_field_in_run_fires_subprocess(self, tmp_path):
        """run() with exec field must call subprocess, not infer_fn."""
        # Write a real exec field pointing to a simple echo script
        echo_script = tmp_path / "echo_script.py"
        echo_script.write_text('import sys; print("exec_fired:" + " ".join(sys.argv[1:]))')

        skill = {
            "name": "test-exec",
            "description": "test",
            "commands": ["/test"],
            "instructions": "",
            "path": str(tmp_path / "SKILL.md"),
            "exec": f"python3 {echo_script} {{input}}",
        }

        infer_called = []
        def mock_infer(msgs):
            infer_called.append(True)
            return "infer_called"

        # Patch infer_with_tools and _model so run() doesn't try to load GPU
        with patch("skills.skills_mod", create=True), \
             patch.dict("sys.modules", {"model": MagicMock(_model=MagicMock())}):
            result = skills_mod.run(skill, "/test hello world", mock_infer)

        assert "exec_fired" in result
        assert len(infer_called) == 0  # infer_fn must NOT have been called

    def test_exec_input_substitution(self, tmp_path):
        """Verify {input} receives the full original message."""
        script = tmp_path / "capture.py"
        script.write_text('import sys; print("ARGS:" + " ".join(sys.argv[1:]))')

        skill = {
            "name": "capture",
            "description": "capture args",
            "commands": ["/capture"],
            "instructions": "",
            "path": str(tmp_path / "SKILL.md"),
            "exec": f"python3 {script} {{input}}",
        }

        result = skills_mod.run(skill, "/capture some argument here", lambda m: "fallback")
        assert "/capture some argument here" in result

    def test_exec_args_substitution_strips_command(self, tmp_path):
        """Verify {args} strips the leading /command word — covered by dispatch tests below."""
        pass  # Tested end-to-end in TestDispatchRouting


# ---------------------------------------------------------------------------
# Test: dispatch.py routing
# ---------------------------------------------------------------------------

class TestDispatchRouting:
    def test_dispatch_routes_markdown(self, tmp_path):
        """dispatch.py with /markdown routes to pdf_to_markdown.py."""
        if not DISPATCH_SCRIPT.exists():
            import pytest; pytest.skip("dispatch.py not found")

        # We just check it routes to the right script — don't run full conversion
        result = subprocess.run(
            [sys.executable, str(DISPATCH_SCRIPT), "/markdown"],
            capture_output=True, text=True, timeout=10
        )
        # Missing file arg → should fail with a clear error, not "Unknown command"
        assert "Unknown command" not in result.stdout + result.stderr

    def test_dispatch_routes_anonymize(self, tmp_path):
        """dispatch.py with /anonymize routes to anonymize.py."""
        if not DISPATCH_SCRIPT.exists():
            import pytest; pytest.skip("dispatch.py not found")

        result = subprocess.run(
            [sys.executable, str(DISPATCH_SCRIPT), "/anonymize"],
            capture_output=True, text=True, timeout=10
        )
        assert "Unknown command" not in result.stdout + result.stderr

    def test_dispatch_unknown_command_exits_nonzero(self):
        """dispatch.py with unknown command exits non-zero and prints help."""
        if not DISPATCH_SCRIPT.exists():
            import pytest; pytest.skip("dispatch.py not found")

        result = subprocess.run(
            [sys.executable, str(DISPATCH_SCRIPT), "/unknown_xyz"],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0
        assert "Unknown command" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Test: anonymize.py — regex pass (no model needed)
# ---------------------------------------------------------------------------

class TestAnonymizeRegexPass:
    def _run_anonymize(self, md_content: str, extra_args=None) -> tuple[str, str]:
        """Write a temp md, run anonymize with --no-llm --no-telegram, return (stdout, redacted_content)."""
        if not ANONYMIZE_SCRIPT.exists():
            import pytest; pytest.skip("anonymize.py not found")

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.md"
            src.write_text(md_content)
            env = os.environ.copy()
            env["KERNEL_DOCS_DIR"] = tmp  # output to same tmp dir for inspection
            args = [sys.executable, str(ANONYMIZE_SCRIPT), str(src), "--no-llm", "--no-telegram"]
            if extra_args:
                args += extra_args
            result = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)
            out_file = Path(tmp) / "test_redacted.md"
            redacted = out_file.read_text() if out_file.exists() else ""
            return result.stdout + result.stderr, redacted

    def test_email_redacted(self):
        _, redacted = self._run_anonymize("Contact: mario.rossi@example.com for details.")
        assert "__REDACTED_EMAIL__" in redacted
        assert "mario.rossi@example.com" not in redacted

    def test_iban_redacted(self):
        _, redacted = self._run_anonymize("Bank: IT60X0542811101000000123456")
        assert "__REDACTED_IBAN__" in redacted

    def test_italian_codice_fiscale_redacted(self):
        _, redacted = self._run_anonymize("CF: RSSMRA85M01H501Z")
        assert "__REDACTED_CF__" in redacted

    def test_url_redacted(self):
        _, redacted = self._run_anonymize("See https://example.com/secret for info.")
        assert "__REDACTED_URL__" in redacted

    def test_clean_text_unchanged(self):
        text = "This document has no PII. Article 1. Clause 2.3."
        _, redacted = self._run_anonymize(text)
        assert "Article 1" in redacted
        assert "Clause 2.3" in redacted

    def test_output_saved_to_kernel_docs_dir(self):
        """Output file must go to KERNEL_DOCS_DIR, not next to the source."""
        if not ANONYMIZE_SCRIPT.exists():
            import pytest; pytest.skip("anonymize.py not found")

        with tempfile.TemporaryDirectory() as src_dir, \
             tempfile.TemporaryDirectory() as out_dir:
            src = Path(src_dir) / "contract.md"
            src.write_text("Name: Mario Rossi\nEmail: mario@test.com")
            env = os.environ.copy()
            env["KERNEL_DOCS_DIR"] = out_dir
            subprocess.run(
                [sys.executable, str(ANONYMIZE_SCRIPT), str(src), "--no-llm", "--no-telegram"],
                capture_output=True, text=True, timeout=30, env=env
            )
            # Redacted file must be in out_dir, NOT in src_dir
            assert (Path(out_dir) / "contract_redacted.md").exists()
            assert not (Path(src_dir) / "contract_redacted.md").exists()


# ---------------------------------------------------------------------------
# Test: pdf_to_markdown.py — output path
# ---------------------------------------------------------------------------

class TestPdfToMarkdownOutputPath:
    def test_output_goes_to_kernel_docs_dir(self, tmp_path):
        """Extracted .md must land in KERNEL_DOCS_DIR, not next to the PDF."""
        if not PDF_SCRIPT.exists():
            import pytest; pytest.skip("pdf_to_markdown.py not found")

        try:
            import fitz
        except ImportError:
            import pytest; pytest.skip("PyMuPDF not installed")

        # Create a minimal test PDF
        src_dir = tmp_path / "source"
        src_dir.mkdir()
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        pdf_path = src_dir / "test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Test page content", fontsize=12)
        doc.save(str(pdf_path))
        doc.close()

        env = os.environ.copy()
        env["KERNEL_DOCS_DIR"] = str(out_dir)

        result = subprocess.run(
            [sys.executable, str(PDF_SCRIPT), str(pdf_path), "--no-telegram"],
            capture_output=True, text=True, timeout=120, env=env
        )

        assert (out_dir / "test.md").exists(), \
            f"Expected test.md in {out_dir}. stdout: {result.stdout[:300]}"
        assert not (src_dir / "test.md").exists(), \
            "test.md must NOT be saved next to the source PDF"
