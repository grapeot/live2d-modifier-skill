from pathlib import Path


def test_skill_is_discoverable():
    root = Path(__file__).resolve().parents[1]
    text = (root / "skills/live2d-modifier/SKILL.md").read_text()
    assert "name: live2d-modifier" in text
    assert "transfer" in text and "residual_holes" in text
