"""B4_Lexical_Lattice_Heuristic: Non-LLM TF-IDF + lexical inclusion + ontology tree depth baseline."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from clasp_ont.ingest.preclean import NativeHubAudit
from clasp_ont.ingest.sssom_io import MappingRecord
from clasp_ont.symbolic.cegar_loop import ALL_LATTICE_ACTIONS, OracleDecision

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "of", "on", "or", "that", "the", "to", "with", "any", "process", "trait",
    "entity", "structure", "compound", "molecule", "plant", "biological",
}

BROAD_CUES = {"broad", "general", "encompassing", "family", "pathway", "overall", "across", "any"}
SPECIFIC_CUES = {"specific", "subtype", "specialized", "individual", "monomer", "apical", "precursor"}


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) > 1 and w not in STOPWORDS]


def score_lexical_lattice_heuristic(
    native_audit: NativeHubAudit,
    mappings: Sequence[MappingRecord],
    conflict_traces: dict[str, list[str]] | None = None,
) -> dict[str, OracleDecision]:
    """Compute calibrated non-LLM action probabilities across the 5-element lattice L(m_i)."""
    traces = conflict_traces or {}

    # Build corpus IDF weights across all classes in native_audit
    doc_freq: Counter[str] = Counter()
    n_docs = max(1, len(native_audit.term_metadata))
    for tm in native_audit.term_metadata.values():
        doc_tokens = set(_tokenize(f"{tm.label} {' '.join(tm.synonyms)} {tm.definition}"))
        for tok in doc_tokens:
            doc_freq[tok] += 1

    def _tfidf_vec(text: str) -> dict[str, float]:
        toks = _tokenize(text)
        if not toks:
            return {}
        tf = Counter(toks)
        vec: dict[str, float] = {}
        for w, c in tf.items():
            idf = math.log((1.0 + n_docs) / (1.0 + doc_freq.get(w, 0))) + 1.0
            vec[w] = (c / len(toks)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        return vec

    def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
        if not v1 or not v2:
            return 0.0
        if len(v1) > len(v2):
            v1, v2 = v2, v1
        return sum(val * v2.get(k, 0.0) for k, val in v1.items())

    decisions: dict[str, OracleDecision] = {}
    for m in mappings:
        s_meta = native_audit.term_metadata.get(m.subject_id)
        o_meta = native_audit.term_metadata.get(m.object_id)

        s_lbl = (s_meta.label if s_meta else m.subject_label) or ""
        o_lbl = (o_meta.label if o_meta else m.object_label) or ""
        s_def = s_meta.definition if s_meta else ""
        o_def = o_meta.definition if o_meta else ""
        s_syn = " ".join(s_meta.synonyms) if s_meta else ""
        o_syn = " ".join(o_meta.synonyms) if o_meta else ""

        s_text = f"{s_lbl} {s_syn} {s_def}"
        o_text = f"{o_lbl} {o_syn} {o_def}"
        sim = _cosine(_tfidf_vec(s_text), _tfidf_vec(o_text))

        s_lbl_toks = set(_tokenize(s_lbl))
        o_lbl_toks = set(_tokenize(o_lbl))

        # Specificity asymmetry: deeper in tree, fewer descendants, longer modifier set -> narrower concept!
        s_depth = s_meta.depth if s_meta else 2
        o_depth = o_meta.depth if o_meta else 2
        s_desc = s_meta.descendant_count if s_meta else 0
        o_desc = o_meta.descendant_count if o_meta else 0

        depth_diff = (s_depth - o_depth) * 0.55
        desc_diff = (math.log1p(o_desc) - math.log1p(s_desc)) * 0.45

        # Lexical modifier inclusion: if o_lbl_toks subset of s_lbl_toks, s is more specific (s <= o -> downgrade_sub)
        mod_bias = 0.0
        if s_lbl_toks and o_lbl_toks:
            if o_lbl_toks < s_lbl_toks:
                mod_bias += 0.9
            elif s_lbl_toks < o_lbl_toks:
                mod_bias -= 0.9
            else:
                mod_bias += 0.18 * (len(s_lbl_toks) - len(o_lbl_toks))

        # Definition cue words ("specific", "broad", "general", "pathway")
        s_all_words = set(re.findall(r"[a-z]+", s_text.lower()))
        o_all_words = set(re.findall(r"[a-z]+", o_text.lower()))
        cue_bias = (
            0.40 * (len(s_all_words & SPECIFIC_CUES) - len(o_all_words & SPECIFIC_CUES))
            + 0.45 * (len(o_all_words & BROAD_CUES) - len(s_all_words & BROAD_CUES))
        )

        # Positive spec_score => subject is narrower than object (s <= o, downgrade_sub)
        # Negative spec_score => object is narrower than subject (o <= s, downgrade_sup)
        spec_score = depth_diff + desc_diff + mod_bias + cue_bias

        # Inspect directional conflict core trace cues if available
        m_traces = " ".join(traces.get(m.mapping_id, []))
        sub_conflict_pen = -0.75 if f"{m.mapping_id}:dir_sub" in m_traces and f"{m.mapping_id}:dir_sup" not in m_traces else 0.0
        sup_conflict_pen = -0.75 if f"{m.mapping_id}:dir_sup" in m_traces and f"{m.mapping_id}:dir_sub" not in m_traces else 0.0
        both_dir_conflict = (f"{m.mapping_id}:dir_sub" in m_traces and f"{m.mapping_id}:dir_sup" in m_traces)

        # Logits across the 5 lattice moves
        is_already_dir = m.predicate_id in {"skos:broadMatch", "skos:narrowMatch"}
        z_keep = 1.6 * sim + (1.8 if is_already_dir else 0.8) * m.confidence - (
            0.0 if is_already_dir else (0.6 if m_traces else -0.8)
        )
        z_sub = 1.2 * sim + 0.95 * spec_score + sub_conflict_pen
        z_sup = 1.2 * sim - 0.95 * spec_score + sup_conflict_pen
        z_weak = 0.85 * sim + (0.95 if both_dir_conflict else -0.15)
        # Low definition similarity (homonym / acronym trap!) boosts negate_drop
        z_drop = (2.1 * (0.28 - sim)) + (0.65 if both_dir_conflict else -0.5)

        logits = {
            "keep_equiv": z_keep,
            "downgrade_sub": z_sub,
            "downgrade_sup": z_sup,
            "weaken_ancestor": z_weak,
            "negate_drop": z_drop,
        }
        max_z = max(logits.values())
        exps = {k: math.exp(v - max_z) for k, v in logits.items()}
        total = sum(exps.values())
        probs = {k: round(exps[k] / total, 6) for k in ALL_LATTICE_ACTIONS}

        # Preferred weaken direction follows specificity orientation when parent exists
        has_o_p1 = bool(o_meta and o_meta.parent1_id) or bool(m.object_parent1_id)
        has_s_p1 = bool(s_meta and s_meta.parent1_id) or bool(m.subject_parent1_id)
        if spec_score >= 0 and has_o_p1:
            w_dir = "sub_to_obj_parent"
        elif spec_score < 0 and has_s_p1:
            w_dir = "obj_to_sub_parent"
        elif has_o_p1:
            w_dir = "sub_to_obj_parent"
        else:
            w_dir = "obj_to_sub_parent"

        top_act = max(probs.items(), key=lambda kv: kv[1])[0]
        rationale = (
            f"B4 lexical/depth heuristic (tfidf_sim={sim:.2f}, depth={s_depth}->{o_depth}, "
            f"spec={spec_score:+.2f}) favors {top_act}."
        )
        decisions[m.mapping_id] = OracleDecision(
            mapping_id=m.mapping_id,
            probs=probs,
            preferred_weaken_direction=w_dir,
            rationale=rationale[:280],
        )
    return decisions
