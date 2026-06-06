"""Tools & methods taxonomy (Phase 8).

Builds the Axis-2 tool/method taxonomy against the canonical operating spec
``docs/tool-classifier-spec.md`` (v3). The four orthogonal axes — disposition
(is it ours?), kind (what is it?), supercategory (what's it for?), salience
(how distinctive?) — plus two persistent match-or-mint registries:

  - the canonical-tool registry (§8) — the leaves; ``ToolRegistry``
  - the method-family registry (§7) — the workhorse; ``FamilyRegistry``

Both accrete by match-or-mint against durable opaque ids (§7/§8, D-06): ids are
minted once and NEVER recomputed from name or membership. See ``vocab`` for the
frozen vocabularies (closed sets — classify into them, never invent values).
"""
