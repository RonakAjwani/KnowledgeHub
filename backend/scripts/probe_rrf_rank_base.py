"""Settle whether Qdrant's RRF ranks from 0 or 1 — and therefore what RRF_MAX is.

``RRF_MAX`` is the analytic ceiling every un-reranked G2 threshold is measured
against (invariant I7). For weighted RRF over two branches it is::

    RRF_MAX = (w_dense + w_sparse) / (k + rank_base)

Both weights and ``k`` are ours to set. ``rank_base`` is Qdrant's, and it is not
published: if the server ranks the top hit as #1 the denominator is ``k + 1``; if
it ranks it as #0 the denominator is ``k``. The contract deliberately left this as
an empirical check rather than an assumption.

The method is the contract's own: find a chunk that ranks **first in both
branches independently**, then read its fused score. That score *is* ``RRF_MAX``,
because a chunk cannot do better than topping every branch — so comparing it
against the two candidate formulas settles the rank base directly.

Run against a live Qdrant:

    docker compose up -d qdrant
    poetry run python scripts/probe_rrf_rank_base.py

Re-run it after any Qdrant version bump. A silent change here would leave every
`FLOOR_FUSED` comparison subtly wrong while looking perfectly healthy.
"""

from __future__ import annotations

import asyncio
import uuid

from app.config import Settings
from app.ingest.embed import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, Embedder
from app.models.schemas import Chunk
from app.retrieval.qdrant_store import QdrantStore

# Deliberately distinctive documents: one carries a rare identifier that both a
# lexical and a semantic match should agree on, so a clear both-branches winner
# exists rather than a near-tie that could not settle anything.
CORPUS = [
    "The ZX9-4471 pressure valve regulates coolant flow in the secondary loop.",
    "Quarterly revenue for the cloud segment reached eight million dollars.",
    "Employees may carry over up to five unused vacation days into January.",
    "The building's fire suppression system is inspected twice per year.",
    "Network latency to the eu-west region averages twelve milliseconds.",
]
QUERY = "ZX9-4471 pressure valve coolant"


async def main() -> int:
    settings = Settings(
        qdrant_url="http://localhost:6333",
        qdrant_collection=f"probe_rrf_{uuid.uuid4().hex[:8]}",
        rrf_k=60,
        w_dense=1.0,
        w_sparse=1.0,
    )
    store = QdrantStore(settings)
    embedder = Embedder(settings)
    user = "probe-user"

    await store.ensure_collection(dense_dim=embedder.dense_dimension)
    try:
        chunks = [
            Chunk(
                id=f"probe{i:020d}",
                doc_id="probe-doc",
                user_id=user,
                chunk_index=i,
                text=text,
                char_start=0,
                char_end=len(text),
                parent_text=text,
                parent_char_start=0,
                parent_char_end=len(text),
            )
            for i, text in enumerate(CORPUS)
        ]
        await store.upsert_chunks(chunks, embedder.embed_documents([c.text for c in chunks]))

        query = embedder.embed_query(QUERY)
        dense = await store.branch_search(
            query, user_id=user, branch=DENSE_VECTOR_NAME, limit=10
        )
        sparse = await store.branch_search(
            query, user_id=user, branch=SPARSE_VECTOR_NAME, limit=10
        )
        fused = await store.hybrid_search(query, user_id=user, limit=10)

        print(f"query: {QUERY!r}\n")
        print(f"dense  #1: {dense[0].chunk_id}  score={dense[0].score:.6f}")
        print(f"sparse #1: {sparse[0].chunk_id}  score={sparse[0].score:.6f}")
        print(f"fused  #1: {fused[0].chunk_id}  score={fused[0].score:.8f}\n")

        if dense[0].chunk_id != sparse[0].chunk_id:
            print("INCONCLUSIVE: no chunk topped both branches — adjust the corpus.")
            return 2
        if fused[0].chunk_id != dense[0].chunk_id:
            print("INCONCLUSIVE: the both-branches winner is not the fused winner.")
            return 2

        observed = fused[0].score
        k, weight_sum = settings.rrf_k, settings.w_dense + settings.w_sparse
        base_one = weight_sum / (k + 1)
        base_zero = weight_sum / k

        print(f"a chunk ranked #1 in BOTH branches scores {observed:.8f}")
        print(f"  hypothesis rank_base=1 ->(1+1)/(60+1) = {base_one:.8f}")
        print(f"  hypothesis rank_base=0 ->(1+1)/(60+0) = {base_zero:.8f}\n")

        if abs(observed - base_one) < abs(observed - base_zero):
            verdict, confirmed = 1, base_one
        else:
            verdict, confirmed = 0, base_zero

        print(f"VERDICT: Qdrant ranks from {verdict} ->RRF_MAX = {confirmed:.8f}")
        print(f"         set RRF_RANK_BASE={verdict} in config.py")
        if abs(observed - confirmed) > 1e-6:
            print(
                f"         WARNING: residual {abs(observed - confirmed):.2e} - neither "
                "hypothesis fits cleanly; investigate before trusting FLOOR_FUSED."
            )
            return 3
        return 0
    finally:
        await store.client.delete_collection(settings.qdrant_collection)
        await store.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
