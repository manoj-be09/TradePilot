import pytest

from packages.regime import research_identity


def test_calculation_identity_ignores_line_endings_but_detects_source_changes(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(research_identity, "ROOT", tmp_path)
    monkeypatch.setattr(research_identity, "FILES", ("rules.py",))
    source = tmp_path / "rules.py"
    source.write_bytes(b"target = 2\r\n")
    original = research_identity.research_code_digest()
    source.write_bytes(b"target = 2\n")
    assert research_identity.research_code_digest() == original
    source.write_bytes(b"target = 3\n")
    assert research_identity.research_code_digest() != original
    source.unlink()
    with pytest.raises(OSError):
        research_identity.research_code_digest()
