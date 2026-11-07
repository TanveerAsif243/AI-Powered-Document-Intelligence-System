import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="docintel_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("UPLOAD_DIR", f"{_tmp}/uploads")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("INLINE_JOBS", "true")
os.environ.setdefault("USE_LLM", "false")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import random

import pytest

from evaluation import synth


@pytest.fixture
def invoice():
    return synth.make_invoice(random.Random(1), 1)


@pytest.fixture
def digital_pdf(tmp_path, invoice):
    p = tmp_path / "inv.pdf"
    synth.write_digital_pdf(str(p), [invoice], [0])
    return str(p), invoice


@pytest.fixture
def scanned_png(tmp_path, invoice):
    p = tmp_path / "scan.png"
    synth.draw_page(synth.render_lines(invoice, 0), "clean").save(p)
    return str(p), invoice
