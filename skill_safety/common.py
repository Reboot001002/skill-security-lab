import hashlib
import json
import os
from pathlib import Path
import tempfile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def package_files(path):
    path = Path(path)
    result = {}
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink():
            raise ValueError("Skill packages may not contain symlinks")
        if entry.is_file():
            result[entry.relative_to(path).as_posix()] = entry.read_text(encoding="utf-8")
    if "runner.py" not in result or "SKILL.md" not in result:
        raise ValueError("Incomplete skill package")
    return result


def package_hash(path):
    return digest(package_files(path))


def safe_child(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path escapes root")
    return path


def code_hash():
    root = Path(__file__).parent
    return digest({p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py"))})
