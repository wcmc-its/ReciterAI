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
boolean going into a noisy-OR, and `run.py` persists the result as one blended float.
`core_mesh_tree_descriptors` returns the descriptors themselves so the cores model can
carry them as reviewable evidence (`SignalResult.mesh_evidence`, weight 0.00) and a
per-descriptor lift can be measured later. It changes no prior.
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


def core_mesh_tree_descriptors(engine, core_id, pmids: list = None) -> dict:
    """{pmid: [(descriptor_ui, descriptor_label, tree_prefix), ...]} for one core.

    WHICH descriptor fired and WHICH of the core's prefixes it hit — the two facts
    `core_mesh_tree_pmids` throws away by collapsing this join to a membership set, and
    `prefilter_prior` throws away a second time by collapsing THAT to a 0.4 constant
    that is then noisy-OR'd into one float. Neither the reviewer looking at a
    `topicalPrior` chip nor a retrospective analysis can recover a descriptor from that
    float, so nothing about MeSH has ever been auditable or measurable. This is the
    record that makes both possible.

    Nothing here ranks, tiers or weights. The entries are the raw join, deduplicated
    and sorted — deliberately NO specificity/depth/rarity scheme, because none is
    justified yet: the per-descriptor lift table has to be computed first, the way the
    method-family table was, and that needs known pubs this pipeline does not have yet.

    Empty dict when the core has no mapped prefixes (5 of 13 cores), WITHOUT touching
    the engine. `pmids` restricts the scan to a pool (the run's corpus); omit it to
    scan all of person_article_keyword (rarely wanted).

    NO try/except, on purpose. `persist.put_core_usage` REMOVEs every attribute in
    `_OWNED_ATTRS` this run did not produce, so a read that failed soft to empty here
    would be WRITTEN as a wipe of `mesh_evidence` on every previously scored row. Same
    rule as `method_families.load_family_index` and `scan_core_llm_scores`. Unlike the
    S3 artifact there is no "well-formed but empty" floor to enforce: a zero-row answer
    is legitimate for a small `--pmids-file`/`--test` pool, and this index is re-derived
    from reciterdb on EVERY run at no cost, so a bad answer self-heals on the next tick
    rather than needing a stored copy read back.
    """
    from collections import defaultdict  # local: the pure prior stays import-light
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
            # The FIRST prefix the tree number sits under, in the core's own list order.
            # No core's list nests today, so there is nothing for the order to decide.
            # The "" default is unreachable — the WHERE clause already matched one of
            # these prefixes and none of them contains a LIKE wildcard — and is here so
            # a collation quirk cannot take a nightly down over a display string.
            prefix = next((p for p in prefixes if tree.startswith(p)), "")
            hits[str(row.pmid)].add((str(row.DescriptorUI), str(row.Label), prefix))
    # sorted(set): one descriptor carries several tree numbers under the same prefix, so
    # dedupe and give the list a stable order rather than the DB's.
    return {pmid: sorted(rows) for pmid, rows in hits.items()}


def core_mesh_tree_pmids(engine, core_id, pmids: list = None) -> set:
    """Return the subset of `pmids` carrying >=1 descriptor under the core's E-tree prefixes.

    Empty set when the core has no mapped prefixes. `pmids` restricts the scan to a
    pool (the run's corpus); omit it to scan all of person_article_keyword (rarely
    wanted). Matched on DescriptorUI via the validated keyword->Label join.

    Derived from `core_mesh_tree_descriptors` rather than running its own narrower
    query: ONE query builder means the prefilter prior and the persisted descriptor
    evidence can never disagree about which pubs carry the signal. Same join, same
    filter, so the membership set — and therefore every prior, likelihood and band —
    is unchanged.
    """
    return set(core_mesh_tree_descriptors(engine, core_id, pmids))


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
