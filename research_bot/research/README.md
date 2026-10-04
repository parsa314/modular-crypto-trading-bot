# Research boundary

Existing `research_bot.ensemble_*` and versioned experiment modules remain in their
original locations to preserve imports, source fingerprints and scientific lineage.
New research modules belong here. Execution foundations must not import this
namespace, notebooks, training scripts or optional model frameworks.

Stage 0 introduces the boundary; migration of the historical model-serving seam
is deferred to the relevant later stage. No experiment is rerun or promoted here.
