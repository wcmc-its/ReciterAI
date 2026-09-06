"""Soft-prioritizer pre-filter for the batch_screen run-mode.

Attaches a cheap, free prior to each (publication, core) pair BEFORE the Sonnet
title screen. v1 is a **soft prioritizer, not a drop gate**: the prior is recorded
on every screened pair so it can (a) order the Sonnet batches + the curator queue
and (b) let the Option-3 calibration pass measure where a drop threshold could sit
without losing recall. `batch_screen` defaults to a drop threshold of 0.0 — nothing
is dropped — until calibration justifies turning dropping on.

Two free signals are combined (noisy-OR), author outranking topic:
  1. author-affinity (repeat-user prior) — the strongest signal (`signals.author_affinity`).
  2. bare-descriptor MeSH **E-tree** membership — a topical hint that a pub carries a
     descriptor under a core's technique branch of the MeSH tree.

WHY bare descriptors and not MeSH qualifiers/subheadings: qualifiers were measured
(analysis/FINDINGS-cores-mesh-subheadings-2026-06-21.md) and rejected — they only
discriminate Biomedical Imaging, are redundant with descriptors there, are identical
across the genomics family elsewhere, and (as a gate) MEDLINE indexing lag would drop
~half of confirmed pubs. Bare descriptors + their tree numbers are 100% present in
reciterdb today (no out-of-band MEDLINE fetch), so the topical signal stays free.

The descriptor->tree join is reciterdb-native and validated 100%:
  person_article_keyword.keyword (descriptor label)
    -> mesh.Label -> mesh.DescriptorUI
    -> mesh_tree_numbers.TreeNumber
A pub carries a core's topical signal iff >=1 of its descriptors has a tree number
under any of that core's prefixes below.

The same join also answers WHICH descriptor fired, which the prior cannot: it is a
boolean folded into one float. `core_mesh_tree_descriptors` returns the descriptors
themselves so they can be carried as reviewable evidence (`SignalResult.mesh_evidence`,
weight 0.00) and a per-descriptor lift measured later. It changes no prior.
"""
from __future__ import annotations

# Per-core MeSH E-tree prefixes (E = "Analytical, Diagnostic and Therapeutic
# Techniques and Equipment"). Validated against reciterdb.mesh_tree_numbers
# (64,883 rows) 2026-06-21 — each prefix resolves to real tree rows. Only the
# technique families that the empirical probe found genuinely discriminative are
# mapped; cores with no clean MeSH technique branch (1 Bioinformatics, 6
# Biorepository, 7 Metabolic Phenotyping, 8 Microbiome, 10 Immune Monitoring) rely
# on the author-affinity signal + the Sonnet screen alone. Deliberate overlaps
# (3 & 5 share the sequencing branch; 9 & 13 share the chemistry/MS branch) are
# fine for a soft prior — author-affinity and the per-core Sonnet screen disambiguate.
CORE_MESH_TREE_PREFIXES: dict = {
    "2":  ["E01.370.350"],                       # Diagnostic Imaging
    "11": ["E01.370.350.515", "E01.370.225.500"],  # Microscopy
    "4":  ["E05.242"],                           # Flow Cytometry / cytological techniques
    "5":  ["E05.393"],                           # Genetic techniques (sequencing)
    "3":  ["E05.393"],                           # Epigenomics (sequencing-based)
    "9":  ["E05.196"],                           # Chemistry / mass-spectrometry techniques
    "13": ["E05.196"],                           # Proteomics/Metabolomics (MS; alias-only core)
    "12": ["E05.196.867"],                       # Nuclear Magnetic Resonance spectroscopy
}

# Soft-prioritizer prior weights (NOT a drop gate). Author signal outranks the
# topical MeSH-tree hint; combined by noisy-OR so a pair with both is strongest.
_PRIOR_AUTHOR = 0.6
_PRIOR_MESH_TREE = 0.4


def prefilter_prior(has_author_affinity: bool, has_mesh_tree: bool) -> float:
    """Noisy-OR of the present cheap signals -> a [0,1] prior. 0.0 = no free signal.

    both -> 0.76, author-only -> 0.6, mesh-only -> 0.4, neither -> 0.0. Pure: takes
    booleans so it is unit-testable without a DB.
    """
    from pipeline_cores.combine import noisy_or  # light (combine imports only models)

    present = []
    if has_author_affinity:
        present.append(_PRIOR_AUTHOR)
    if has_mesh_tree:
        present.append(_PRIOR_MESH_TREE)
    return round(noisy_or(*present), 4)


# This and `core_mesh_tree_descriptors` below run the SAME join over the same prefixes and
# MUST stay consistent — a prefix, join or filter change belongs in both. Deliberately NOT
# merged into one builder: `batch_screen` calls THIS one with the whole corpus pmid list,
# and deriving the membership set from the per-(pmid, descriptor, tree-number) rows would
# fan that hot path out on a 1 vCPU / 4 GB task for a result it immediately collapses.
def core_mesh_tree_pmids(engine, core_id, pmids: list = None) -> set:
    """Return the subset of `pmids` carrying >=1 descriptor under the core's E-tree prefixes.

    Empty set when the core has no mapped prefixes. `pmids` restricts the scan to a
    pool (the run's corpus); omit it to scan all of person_article_keyword (rarely
    wanted). Matched on DescriptorUI via the validated keyword->Label join.
    """
    from sqlalchemy import bindparam, text  # lazy (keeps the pure prior import-light)

    prefixes = CORE_MESH_TREE_PREFIXES.get(str(core_id), [])
    if not prefixes:
        return set()
    like = " OR ".join(f"mtn.TreeNumber LIKE :p{i}" for i in range(len(prefixes)))
    params = {f"p{i}": pfx + "%" for i, pfx in enumerate(prefixes)}
    sql = (
        "SELECT DISTINCT pak.pmid FROM person_article_keyword pak "
        "JOIN mesh m ON m.Label = pak.keyword "
        "JOIN mesh_tree_numbers mtn ON mtn.DescriptorUI = m.DescriptorUI "
        f"WHERE ({like})"
    )
    binds = []
    if pmids:
        sql += " AND pak.pmid IN :pmids"
        binds.append(bindparam("pmids", expanding=True))
        params["pmids"] = [int(p) for p in pmids]
    stmt = text(sql)
    if binds:
        stmt = stmt.bindparams(*binds)
    with engine.connect() as conn:
        return {str(r.pmid) for r in conn.execute(stmt, params)}


# Same join as `core_mesh_tree_pmids` above, kept separate on purpose — see the note
# there. Keep the two consistent.
def core_mesh_tree_descriptors(engine, core_id, pmids: list = None) -> dict:
    """{pmid: [(descriptor_ui, descriptor_label, tree_prefix), ...]} for one core.

    WHICH descriptor fired — the fact `core_mesh_tree_pmids` collapses to a boolean and
    `prefilter_prior` collapses again into one blended float, from which no descriptor
    can be recovered and no per-descriptor lift ever computed.

    Deliberately NO tiering/specificity/rarity scheme: the per-descriptor lift table has
    to be computed first, the way the method-family one was, and that needs known pubs
    this pipeline does not have yet.

    Empty dict for a core with no mapped prefixes, WITHOUT touching the engine. NO
    try/except on purpose: `persist.put_core_usage` REMOVEs every `_OWNED_ATTRS`
    attribute a run did not produce, so a read failing soft to empty here would be
    WRITTEN as a wipe of `mesh_evidence` on every previously scored row.
    """
    from collections import defaultdict
    from sqlalchemy import bindparam, text  # lazy (keeps the pure prior import-light)

    prefixes = CORE_MESH_TREE_PREFIXES.get(str(core_id), [])
    if not prefixes:
        return {}
    like = " OR ".join(f"mtn.TreeNumber LIKE :p{i}" for i in range(len(prefixes)))
    params = {f"p{i}": pfx + "%" for i, pfx in enumerate(prefixes)}
    sql = (
        "SELECT DISTINCT pak.pmid, m.DescriptorUI, m.Label, mtn.TreeNumber "
        "FROM person_article_keyword pak "
        "JOIN mesh m ON m.Label = pak.keyword "
        "JOIN mesh_tree_numbers mtn ON mtn.DescriptorUI = m.DescriptorUI "
        f"WHERE ({like})"
    )
    binds = []
    if pmids:
        sql += " AND pak.pmid IN :pmids"
        binds.append(bindparam("pmids", expanding=True))
        params["pmids"] = [int(p) for p in pmids]
    stmt = text(sql)
    if binds:
        stmt = stmt.bindparams(*binds)
    hits: dict = defaultdict(set)
    with engine.connect() as conn:
        for row in conn.execute(stmt, params):
            tree = str(row.TreeNumber).upper()
            # First prefix in the core's own list order; no core's list nests today. The
            # "" default is unreachable (the WHERE already matched one, wildcard-free)
            # and is here so a collation quirk cannot take a nightly down over a label.
            prefix = next((p for p in prefixes if tree.startswith(p)), "")
            hits[str(row.pmid)].add((str(row.DescriptorUI), str(row.Label), prefix))
    # sorted(set): one descriptor carries several tree numbers under the same prefix.
    return {pmid: sorted(rows) for pmid, rows in hits.items()}


def compute_priors(pmids: list, *, mesh_pmids: set, author_pmids: set) -> dict:
    """Map pmid -> soft prior float, from precomputed signal membership sets.

    `mesh_pmids` = core_mesh_tree_pmids output; `author_pmids` = pmids whose byline
    has a non-zero author-affinity for the core. Pure given the two sets, so the
    orchestration (DB/DynamoDB reads) stays out of the unit-tested path.
    """
    return {
        str(p): prefilter_prior(str(p) in author_pmids, str(p) in mesh_pmids)
        for p in pmids
    }
