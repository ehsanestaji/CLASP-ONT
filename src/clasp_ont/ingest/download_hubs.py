"""Checksummed public multi-ontology hub downloader and embedded xref crosswalk extractor.

Supports Suite 1 (`OAEI_BioML_2026`), Suite 2 (`Plant_Biodiv_Hub`), and Suite 3 (`Disease_Phenotype_Hub`)
with 100% open redistributable licenses and SHA-256 provenance logging.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

import rdflib
import yaml
from huggingface_hub import hf_hub_download

from clasp_ont.ingest.sssom_io import build_converter, normalize_to_iri

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HUBS_CFG = REPO_ROOT / "configs" / "hubs.yaml"
OBOINOWL_XREF = rdflib.URIRef("http://www.geneontology.org/formats/oboInOwl#hasDbXref")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_url_with_fallback(
    url: str,
    dest: Path,
    expected_sha256: str | None = None,
    fallback_url: str | None = None,
) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        digest = sha256_file(dest)
        if expected_sha256 and digest != expected_sha256:
            dest.unlink()
        else:
            return digest

    urls = [u for u in (url, fallback_url) if u]
    last_err: Exception | None = None
    for candidate in urls:
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": "CLASP-ONT/0.1"})
            with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:
                shutil.copyfileobj(resp, out)
            tmp.replace(dest)
            digest = sha256_file(dest)
            if expected_sha256 and digest != expected_sha256:
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, got {digest}"
                )
            return digest
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            last_err = exc
    raise RuntimeError(f"Failed to download {dest.name} from {urls}: {last_err}")


def extract_obo_dbxrefs_as_pairs(
    ontology_paths: dict[str, Path],
    allowed_iris: set[str],
) -> list[tuple[str, str]]:
    """Extract cross-ontology oboInOwl:hasDbXref assertions resolved against allowed_iris."""
    conv = build_converter()
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _ont_id, owl_path in ontology_paths.items():
        g = rdflib.Graph()
        g.parse(str(owl_path))
        for subj, xref_lit in g.subject_objects(OBOINOWL_XREF):
            s_iri = str(subj)
            if s_iri not in allowed_iris:
                continue
            raw_x = str(xref_lit).strip()
            if ":" not in raw_x or " " in raw_x:
                continue
            o_iri = normalize_to_iri(raw_x, conv)
            if o_iri in allowed_iris and o_iri != s_iri and (s_iri, o_iri) not in seen:
                seen.add((s_iri, o_iri))
                pairs.append((s_iri, o_iri))
    return pairs


def fetch_public_suite(
    suite_id: str,
    raw_root: Path | None = None,
    hubs_cfg_path: Path = DEFAULT_HUBS_CFG,
) -> dict[str, object]:
    """Download and verify raw ontologies and crosswalk files for a public evaluation suite."""
    cfg = yaml.safe_load(hubs_cfg_path.read_text(encoding="utf-8"))
    if suite_id not in cfg["suites"]:
        raise KeyError(f"Unknown suite_id '{suite_id}' in {hubs_cfg_path}")
    scfg = cfg["suites"][suite_id]
    target_dir = (raw_root or (REPO_ROOT / "data" / "raw")) / suite_id
    target_dir.mkdir(parents=True, exist_ok=True)

    provenance: dict[str, str] = {}

    if suite_id == "OAEI_BioML_2026":
        hf_repo = scfg["hf_repo"]
        hf_rev = scfg["hf_revision"]
        for key, fmeta in scfg["hf_files"].items():
            local_p = Path(
                hf_hub_download(
                    repo_id=hf_repo,
                    filename=fmeta["path"],
                    revision=hf_rev,
                    repo_type="dataset",
                    local_dir=str(target_dir / "hf_bioml_2026"),
                )
            )
            digest = sha256_file(local_p)
            expected = fmeta.get("sha256")
            if expected and digest != expected:
                raise ValueError(f"HF checksum mismatch on {key}: expected {expected}, got {digest}")
            provenance[key] = digest

        zmeta = scfg.get("zenodo_bioml_subs", {})
        if zmeta.get("ncit_doid_zip_url"):
            z_dest = target_dir / "zenodo_ncit_doid.zip"
            provenance["zenodo_ncit_doid_zip"] = download_url_with_fallback(
                zmeta["ncit_doid_zip_url"], z_dest
            )
            extract_z = target_dir / "zenodo_ncit_doid"
            if not extract_z.is_dir():
                with zipfile.ZipFile(z_dest, "r") as zf:
                    zf.extractall(extract_z)
    else:
        for ont_id, ometa in scfg.get("ontologies", {}).items():
            dest = target_dir / "ontologies" / f"{ont_id.lower()}.owl"
            provenance[f"ont_{ont_id}"] = download_url_with_fallback(
                ometa["url"],
                dest,
                expected_sha256=ometa.get("sha256"),
                fallback_url=ometa.get("fallback_url"),
            )
        for cw_id, cwmeta in scfg.get("crosswalks", {}).items():
            dest = target_dir / "crosswalks" / f"{cw_id}.sssom.tsv"
            provenance[f"cw_{cw_id}"] = download_url_with_fallback(
                cwmeta["url"],
                dest,
                expected_sha256=cwmeta.get("sha256"),
            )

    manifest_path = target_dir / "provenance_sha256.json"
    manifest_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"suite_id": suite_id, "raw_dir": str(target_dir), "checksums": provenance}
