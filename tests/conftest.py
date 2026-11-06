import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def fake_model(tmp_path):
    """A minimal model folder (no moc content) for file-level helpers."""
    d = tmp_path / "m"
    (d / "tex").mkdir(parents=True)
    Image.fromarray(np.zeros((16, 16, 4), np.uint8)).save(d / "tex/texture_00.png")
    (d / "a.moc3").write_bytes(b"MOC3fake")
    (d / "a.model3.json").write_text(json.dumps({"Version": 3, "FileReferences": {"Moc": "a.moc3", "Textures": ["tex/texture_00.png"]}}))
    (d / "a.vtube.json").write_text(json.dumps({"Name": "orig", "ModelID": "x", "FileReferences": {"Model": "a.model3.json"}}))
    (d / "smile.exp3.json").write_text(json.dumps({"Parameters": [{"Id": "P1", "Value": 0.5, "Blend": "Add"},
                                                                  {"Id": "P2", "Value": 2, "Blend": "Multiply"},
                                                                  {"Id": "P3", "Value": 7, "Blend": "Overwrite"}]}))
    return d


def real_model():
    p = os.environ.get("L2D_TEST_MODEL")
    return Path(p) if p else None


model = pytest.mark.skipif(real_model() is None, reason="set L2D_TEST_MODEL to a real model folder")
browser = pytest.mark.skipif(not os.environ.get("L2D_TEST_BROWSER"), reason="set L2D_TEST_BROWSER=1 to run headless Chrome tests")
