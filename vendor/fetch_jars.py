#!/usr/bin/env python3
"""Checksummed downloader and compiler for headless JVM ontology reasoners (ROBOT/ELK & LogMap).

Strictly respects the -Xmx4G JVM ceiling and requires zero GPU/LLM weights.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent

ARTIFACTS = {
    "robot.jar": {
        "url": "https://github.com/ontodev/robot/releases/download/v1.9.11/robot.jar",
        "sha256": "53cd275c8e4c0dad1fc2c1c34174876795cd7d6b6926e1444adcbb0cb3c7ab26",
    },
    "logmap-matcher-standalone-july-2021.zip": {
        "url": "https://github.com/ernestojimenezruiz/logmap-matcher/releases/download/logmap-matcher-july-2021/logmap-matcher-standalone-july-2021.zip",
        "sha256": "b807c02092e420562e0e010fedfc04f146bca3deb2dbc076ac9caab4237cd3f2",
    },
}

PINNED_SHA256_FILE = VENDOR_DIR / "jar_checksums.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_jdk_tool(tool_name: str = "java") -> Path:
    """Locate a working OpenJDK 17+ binary (avoiding broken macOS /usr/bin/java stubs)."""
    candidates: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / tool_name)
    candidates.extend(
        [
            Path(f"/opt/homebrew/opt/openjdk@21/bin/{tool_name}"),
            Path(f"/opt/homebrew/opt/openjdk/bin/{tool_name}"),
            Path(f"/usr/local/opt/openjdk@21/bin/{tool_name}"),
            Path(f"/usr/local/opt/openjdk/bin/{tool_name}"),
        ]
    )
    which_hit = shutil.which(tool_name)
    if which_hit:
        candidates.append(Path(which_hit))

    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            proc = subprocess.run(
                [str(cand), "-version"],
                capture_output=True,
                text=True,
                env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx512M"},
            )
            if proc.returncode == 0:
                return cand
    raise RuntimeError(
        f"No working OpenJDK 17+ '{tool_name}' runtime found. Set JAVA_HOME or install openjdk@21."
    )


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "CLASP-ONT/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def fetch_all(verify_only: bool = False) -> dict[str, str]:
    import json

    pinned: dict[str, str] = {}
    if PINNED_SHA256_FILE.is_file():
        pinned = json.loads(PINNED_SHA256_FILE.read_text(encoding="utf-8"))

    observed: dict[str, str] = {}
    for filename, meta in ARTIFACTS.items():
        dest = VENDOR_DIR / filename
        expected = meta.get("sha256") or pinned.get(filename)
        if not dest.is_file():
            if verify_only:
                raise FileNotFoundError(f"Missing required vendor artifact: {dest}")
            print(f"[fetch_jars] Downloading {filename} from {meta['url']} ...")
            download_file(meta["url"], dest)
        digest = sha256_file(dest)
        if expected and digest != expected:
            raise ValueError(
                f"SHA-256 mismatch for {filename}: expected {expected}, got {digest}"
            )
        observed[filename] = digest
        print(f"[fetch_jars] Verified {filename} (sha256={digest[:16]}...)")

    # Extract logmap standalone zip if not extracted
    zip_path = VENDOR_DIR / "logmap-matcher-standalone-july-2021.zip"
    extract_dir = VENDOR_DIR / "logmap-standalone"
    if zip_path.is_file() and not extract_dir.is_dir():
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        # Copy primary jar to vendor/logmap-matcher.jar if present
        jars = sorted(extract_dir.rglob("*logmap*matcher*.jar"))
        if jars:
            shutil.copy2(jars[0], VENDOR_DIR / "logmap-matcher.jar")
            observed["logmap-matcher.jar"] = sha256_file(VENDOR_DIR / "logmap-matcher.jar")

    if not PINNED_SHA256_FILE.is_file() and not verify_only:
        PINNED_SHA256_FILE.write_text(
            json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    helper_src = VENDOR_DIR / "ElkClosureRunner.java"
    if helper_src.is_file():
        compile_elk_helper(helper_src)

    return observed


def compile_elk_helper(java_src: Path) -> Path:
    javac = find_jdk_tool("javac")
    robot_jar = VENDOR_DIR / "robot.jar"
    classes_dir = VENDOR_DIR / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)
    class_file = classes_dir / "ElkClosureRunner.class"
    if class_file.is_file() and class_file.stat().st_mtime >= java_src.stat().st_mtime:
        return classes_dir
    cmd = [
        str(javac),
        "--release",
        "17",
        "-cp",
        str(robot_jar),
        "-d",
        str(classes_dir),
        str(java_src),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "JAVA_TOOL_OPTIONS": "-Xmx1G"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to compile {java_src}:\n{proc.stderr}")
    return classes_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and checksum ROBOT and LogMap JARs")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    fetch_all(verify_only=args.verify_only)


if __name__ == "__main__":
    main()
