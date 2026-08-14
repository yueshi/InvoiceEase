import pytest

from invoicing.storage import LocalFileStorage


@pytest.fixture()
def storage(tmp_path):
    return LocalFileStorage(root=str(tmp_path / "originals"))


def test_put_and_get_roundtrip(storage):
    key = storage.put("test/hello.xml", b"<eInvoice/>", "application/xml")
    assert key == "test/hello.xml"
    assert storage.get(key) == b"<eInvoice/>"


def test_put_creates_nested_dirs(storage, tmp_path):
    storage.put("a/b/c.pdf", b"%PDF-1.4", "application/pdf")
    assert (tmp_path / "originals" / "a" / "b" / "c.pdf").read_bytes() == b"%PDF-1.4"


def test_path_traversal_rejected(storage):
    with pytest.raises(ValueError, match="非法路径"):
        storage.put("../escape.pdf", b"x", "application/pdf")


def test_get_missing_raises(storage):
    with pytest.raises(FileNotFoundError):
        storage.get("nope/missing.xml")
