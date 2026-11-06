"""Ensure releases contain software rather than model assets or runtime secrets."""
import sys
import tarfile
from pathlib import Path
import zipfile

for file in Path(sys.argv[1]).iterdir():
    if file.suffix == ".whl":
        with zipfile.ZipFile(file) as archive:
            names = archive.namelist()
    elif file.name.endswith(".tar.gz"):
        with tarfile.open(file) as archive:
            names = archive.getnames()
    else:
        continue
    blocked = [name for name in names if any(part in {"local", "node_modules", ".env", "work"}
                                            for part in Path(name).parts)
               or name.lower().endswith((".moc3", ".cmo3", ".psd", ".png", ".jpg", ".jpeg", ".mp4"))
               or "live2dcubismcore" in name.lower()]
    if blocked:
        raise SystemExit(f"Unexpected non-software assets in {file.name}: {blocked}")
    print(f"{file.name}: checked {len(names)} entries")
