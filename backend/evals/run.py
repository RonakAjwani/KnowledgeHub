"""Run the golden set against the ingested corpus and report.

Grading is deliberately crude on the answerable side — substring presence rather
than a judge model. A judge would be more nuanced and would also be the thing
under test: if the pipeline and the grader share a model and a prompt style,
agreement between them measures consistency, not correctness. A number that
appears or does not appear is a fact.

The unanswerable side is graded on behaviour, not content: did the system
decline? That is the measurement the relevance floors exist to serve, and it is
the one most likely to transfer to a corpus this was not tuned on.

    PYTHONPATH=. poetry run python -m evals.run              # everything
    PYTHONPATH=. poetry run python -m evals.run --section E  # one section
    PYTHONPATH=. poetry run python -m evals.run --ids A1,A9
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import pathlib
import re
import sys
import time
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import models as db
from app.graph.build import build_graph
from app.graph.nodes import (
    Deps,
    _search,
    build_generate_messages,
    context_budget,
    grade_node,
    rerank_node,
    retrieve_node,
)
from app.graph.state import Grade, initial_state
from app.graph.verify import split_claims
from app.ingest.embed import Embedder
from app.llm.client import LLMClient
from app.memory.conversation import load_memory
from app.retrieval.hydrate import hydrate_candidates
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.rerank import Reranker
from evals.corpus import BY_FILENAME, BY_KEY
from evals.questions import QUESTIONS, Expect, Question

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Windows consoles default to cp1252, and models emit typographic whitespace —
# gpt-oss-20b writes "July 2026" with a narrow no-break space. Printing an
# answer then kills the run *after* the LLM tokens were spent, losing every
# result behind it. Degrade the character rather than the run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

EVAL_USER = "eval-user"
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "evals" / "results"

# Phrases a declining answer uses. Matching on the *shape* of a refusal rather
# than an exact sentence, because the model writes its own words and the
# abstain node's text is only one of the ways a turn can decline.
_DECLINE_MARKERS = (
    "could not find",
    "couldn't find",
    "do not contain",
    "does not contain",
    "doesn't contain",
    "don't contain",
    "not provided",
    "not specified",
    "not mentioned",
    "not disclosed",
    "not stated",
    "not reported",
    # The inflected forms. "not reported" was present and "do not report" was
    # not, so E6 — "The documents you provided do not report an F1 score" — was
    # scored as a hallucination for declining exactly as intended. A refusal the
    # grader cannot see is worse than no grader: it moves the number in the
    # direction that flatters nothing and blames the wrong stage.
    "do not report",
    "does not report",
    "doesn't report",
    "did not report",
    "do not provide",
    "does not provide",
    "not include",
    "no information",
    "not available in",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "not present in",
    "not found in",
    "does not appear",
    "no mention",
)


@dataclass
class Outcome:
    id: str
    section: str
    expect: str
    passed: bool
    declined: bool
    grade: str
    relevance: float
    rerank_status: str
    sub_queries: int
    citations: int
    docs_cited: list[str]
    latency_s: float
    answer: str
    missing: list[str]
    degradations: list[str]
    # Which generation model answered. Recorded because eval load is spread
    # across Groq's per-model daily buckets, and an accuracy number that mixes
    # models without saying so is not a number about this pipeline.
    model: str = ""
    # Did retrieval put a chunk from every document the question needs into the
    # context the model actually saw? Separates "retrieval missed it" from "the
    # passage was there and the model did not use it" — the two failures a
    # single accuracy score collapses into one.
    docs_expected: list[str] = field(default_factory=list)
    retrieved_expected: bool = True
    # --- citation faithfulness (measured without a judge; see cite_support)
    claims: int = 0
    claims_cited: int = 0
    dangling: int = 0
    facts_checked: int = 0
    facts_supported: int = 0


# doc_id -> corpus key, resolved once from Postgres at startup. Without it
# `docs_cited` was an eight-character uuid prefix nobody could map to a file.
DOC_KEYS: dict[str, str] = {}


def doc_key(doc_id: str) -> str:
    return DOC_KEYS.get(doc_id, doc_id[:8])


def looks_like_decline(answer: str, grade: str) -> bool:
    if grade == str(Grade.ABSTAIN):
        return True
    lowered = answer.lower()
    return any(marker in lowered for marker in _DECLINE_MARKERS)


_NUMERIC = re.compile(r"^\d[\d,.]*$")


def present(needle: str, answer_lower: str) -> bool:
    """Is this expected fact in the answer?

    Text is matched as a plain substring — the model's phrasing is its own.
    Numbers are matched on a **number boundary**, because a raw substring test
    silently inflates the score: "75" is inside "175", "46" is inside "460", and
    "0.7" is inside "0.75". Every one of those is a different fact, and several
    of them appear in this corpus. Thousands separators are optional on both
    sides, so a golden "6,634" is satisfied by an answer that writes "6634".
    """
    needle = needle.strip()
    if not _NUMERIC.match(needle):
        return needle.lower() in answer_lower
    variants = {needle, needle.replace(",", "")}
    return any(
        re.search(rf"(?<![\d.]){re.escape(v)}(?!\d)", answer_lower) for v in variants
    )


def grade_answer(question: Question, answer: str, grade: str) -> tuple[bool, bool, list[str]]:
    """Returns (passed, declined, missing_substrings)."""
    declined = looks_like_decline(answer, grade)

    if question.expect is Expect.DECLINE:
        # The whole test. A confident, specific, plausible answer here is a
        # failure however well it reads.
        return declined, declined, []

    lowered = answer.lower()
    # `must_include_all` is a conjunction — every part of a multi-part question
    # has to be answered. `must_include` is a disjunction: any one hit means the
    # fact is present, under a different wording.
    missing = [s for s in question.must_include_all if not present(s, lowered)]
    hit = not missing
    if question.must_include:
        absent = [s for s in question.must_include if not present(s, lowered)]
        if len(absent) == len(question.must_include):
            missing += absent
            hit = False
    return (hit and not declined), declined, missing


def cite_support(
    question: Question,
    answer: str,
    by_marker: dict[int, str],
    text_by_id: dict[str, str],
) -> tuple[int, int, int, int, int]:
    """Does [n] actually support the sentence it is attached to?

    Returns ``(claims, claims_cited, dangling, facts_checked, facts_supported)``.

    ``verify_api.py`` already proves a citation resolves to real characters in
    ``normalized_text``. That is a different question from whether those
    characters *back the claim* — a marker pointing at a real but irrelevant
    passage passes the resolution check and is still a false citation.

    Measured **without a judge**, deliberately. For every expected fact the
    answer actually states, find the claim stating it and check that *that
    claim's own* citations contain the fact. A number is either in the cited
    passage or it is not, so the measurement does not move when the generation
    model changes, and a judge sharing the pipeline's model cannot launder its
    own mistakes into a score. The cost is coverage, not correctness: it can
    only check facts the golden set names, which is why `facts_checked` is
    reported next to `facts_supported` rather than folded into a ratio.
    """
    claim_list = split_claims(answer)
    cited = [c for c in claim_list if c.markers]
    dangling = sum(1 for c in cited for m in c.markers if m not in by_marker)

    checked = supported = 0
    for needle in (question.must_include_all or question.must_include):
        for claim in claim_list:
            if not present(needle, claim.text.lower()):
                continue
            checked += 1
            evidence = " ".join(
                text_by_id.get(by_marker[m], "") for m in claim.markers if m in by_marker
            )
            supported += present(needle, evidence.lower())
            break
    return len(claim_list), len(cited), dangling, checked, supported


async def run_retrieval_only(
    question: Question, deps: Deps, settings: Settings
) -> Outcome:
    """Everything up to and including the gate, with no generation call.

    The floors are a property of ``grade``, which decides answer-versus-abstain
    *before* ``generate`` ever runs — so the tuning signal costs zero LLM calls.
    That matters practically (Gemini's free tier is 20 requests/day on the
    generate model, far short of a 55-question sweep) and methodologically: the
    measurement no longer moves when the generation model changes underneath it.

    ``route`` and ``rewrite`` are skipped rather than mocked. Both fail open — to
    ``retrieve`` and to the raw query respectively — so skipping them reproduces
    their degraded path exactly rather than approximating it.
    """
    started = time.time()
    state = dict(
        initial_state(
            user_id=EVAL_USER,
            conversation_id=f"eval-{question.id}",
            raw_query=question.text,
        )
    )

    state["raw_candidates"] = await _search(state, deps, question.text)
    state["rewritten"] = False

    for node in (retrieve_node, rerank_node, grade_node):
        state.update(await node(state, deps))

    elapsed = time.time() - started
    grade = str(state.get("grade", ""))
    # No answer text exists, so "declined" is the gate's decision alone.
    declined = grade == str(Grade.ABSTAIN)
    passed = declined if question.expect is Expect.DECLINE else not declined

    used = state.get("candidates", [])[: context_budget(state, settings)]
    cited = sorted({doc_key(c.chunk.doc_id) for c in used})
    return Outcome(
        id=question.id,
        section=question.section,
        expect=str(question.expect),
        passed=passed,
        declined=declined,
        grade=grade,
        relevance=round(state.get("relevance", 0.0), 4),
        rerank_status=state.get("rerank_status", ""),
        sub_queries=1,
        citations=len(used),
        docs_cited=cited,
        latency_s=round(elapsed, 1),
        answer="",
        missing=[],
        degradations=[f"{d.stage}/{d.reason}" for d in state.get("degradations", [])],
        model="",
        docs_expected=list(question.docs),
        retrieved_expected=set(question.docs) <= set(cited),
    )


async def run_question(
    question: Question, deps: Deps, settings: Settings, session
) -> Outcome:
    graph = build_graph(deps)
    started = time.time()

    state = initial_state(
        user_id=EVAL_USER,
        conversation_id=f"eval-{question.id}",
        raw_query=question.text,
    )
    result = await graph.ainvoke(state)

    # Multi-turn questions continue in the same conversation with the memory the
    # first turn produced.
    for follow_up in question.follow_ups:
        memory = await load_memory(
            session, conversation_id=f"eval-{question.id}", user_id=EVAL_USER
        )
        follow_state = initial_state(
            user_id=EVAL_USER,
            conversation_id=f"eval-{question.id}",
            raw_query=follow_up,
            recent_turns=[
                {"role": "user", "content": question.text},
                {"role": "assistant", "content": result.get("answer", "")},
            ],
            rolling_summary=memory.rolling_summary,
            entity_ledger=memory.entity_ledger,
        )
        result = await graph.ainvoke(follow_state)

    elapsed = time.time() - started
    answer = result.get("answer", "")
    grade = str(result.get("grade", ""))
    passed, declined, missing = grade_answer(question, answer, grade)

    used = result.get("candidates", [])[: context_budget(result, settings)]
    cited = sorted({doc_key(c.chunk.doc_id) for c in used})

    # Re-derive the marker -> chunk map with the same pure function `generate`
    # used, so the mapping is the real one rather than a reconstruction. No LLM
    # call: build_generate_messages only assembles a prompt.
    _, chunk_ids = build_generate_messages(result)
    by_marker = {i + 1: cid for i, cid in enumerate(chunk_ids)}
    text_by_id = {
        c.chunk.id: (c.chunk.parent_text or c.chunk.text)
        for c in result.get("candidates", [])
    }
    claims, claims_cited, dangling, checked, supported = cite_support(
        question, answer, by_marker, text_by_id
    )

    return Outcome(
        id=question.id,
        section=question.section,
        expect=str(question.expect),
        passed=passed,
        declined=declined,
        grade=grade,
        relevance=round(result.get("relevance", 0.0), 4),
        rerank_status=result.get("rerank_status", ""),
        sub_queries=len(result.get("effective_queries") or [1]),
        citations=len(used),
        docs_cited=cited,
        latency_s=round(elapsed, 1),
        answer=answer.replace("\n", " ")[:400],
        missing=missing,
        degradations=[f"{d.stage}/{d.reason}" for d in result.get("degradations", [])],
        model=settings.llm_model_generate,
        docs_expected=list(question.docs),
        retrieved_expected=set(question.docs) <= set(cited),
        claims=claims,
        claims_cited=claims_cited,
        dangling=dangling,
        facts_checked=checked,
        facts_supported=supported,
    )


async def _resolve_corpus(maker, selected: list[Question]) -> int:
    """Map ingested doc ids to corpus keys, and refuse to run on a wrong corpus.

    A question naming a document that is not ingested fails exactly like a
    retrieval miss. That confusion cost a session once already, so it is now an
    up-front error rather than a mystery in the results table.
    """
    async with maker() as session:
        rows = await session.execute(
            select(db.Document).where(
                db.Document.user_id == EVAL_USER, db.Document.status == "ready"
            )
        )
        documents = rows.scalars().all()

    ingested: set[str] = set()
    for document in documents:
        entry = BY_FILENAME.get(document.filename)
        if entry is None:
            print(f"ingested but not declared in evals.corpus: {document.filename}")
            return 1
        DOC_KEYS[str(document.id)] = entry.key
        ingested.add(entry.key)

    wanted = {key for question in selected for key in question.docs}
    unknown = wanted - set(BY_KEY)
    if unknown:
        print(f"questions name undeclared documents: {sorted(unknown)}")
        return 1
    absent = wanted - ingested
    if absent:
        print(f"questions need documents that are not ingested: {sorted(absent)}")
        print("run: PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset")
        return 1
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", help="only run one section, e.g. E")
    parser.add_argument("--ids", help="comma-separated question ids")
    parser.add_argument("--floor-fused", type=float)
    parser.add_argument("--floor-rerank", type=float)
    parser.add_argument("--tag", default="run", help="label for the results file")
    parser.add_argument(
        "--generate-model",
        help="pin the generation model, e.g. qwen/qwen3.6-27b. Groq meters each "
        "model on its own daily bucket, so an iteration run can be paid for out "
        "of a different one than the headline number",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="stop after the gate; no generation call, so the floors can be swept "
        "without spending LLM quota",
    )
    args = parser.parse_args()

    settings = Settings(
        qdrant_collection="eval_chunks",
        max_escalated_pages=16,
        **{
            k: v
            for k, v in (
                ("floor_fused", args.floor_fused),
                ("floor_rerank", args.floor_rerank),
                ("llm_model_generate", args.generate_model),
            )
            if v is not None
        },
    )

    selected = QUESTIONS
    if args.section:
        selected = [q for q in selected if q.section == args.section.upper()]
    if args.ids:
        wanted = {i.strip().upper() for i in args.ids.split(",")}
        selected = [q for q in selected if q.id.upper() in wanted]
    if not selected:
        print("no questions matched")
        return 1

    store = QdrantStore(settings)
    embedder = Embedder(settings)
    llm = LLMClient(settings)
    reranker = Reranker(settings)
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    if await _resolve_corpus(maker, selected) != 0:
        await llm.aclose()
        await reranker.aclose()
        await store.aclose()
        await engine.dispose()
        return 1

    print(
        f"running {len(selected)} questions  model={settings.llm_model_generate}  "
        f"floor_fused={settings.floor_fused} floor_rerank={settings.floor_rerank}\n"
    )
    print(f"{'id':<5} {'exp':<8} {'res':<5} {'grade':<8} {'rel':>6} {'rerank':<17} "
          f"{'q':>2} {'cite':>4} {'sec':>5}  answer")
    print("-" * 150)

    outcomes: list[Outcome] = []
    async with maker() as session:
        deps = Deps(
            llm=llm,
            store=store,
            embedder=embedder,
            reranker=reranker,
            settings=settings,
            hydrate=functools.partial(hydrate_candidates, session),
        )
        for question in selected:
            try:
                outcome = (
                    await run_retrieval_only(question, deps, settings)
                    if args.retrieval_only
                    else await run_question(question, deps, settings, session)
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{question.id:<5} ERROR {type(exc).__name__}: {str(exc)[:90]}")
                continue
            outcomes.append(outcome)
            mark = "PASS " if outcome.passed else "FAIL "
            print(
                f"{outcome.id:<5} {outcome.expect:<8} {mark:<5} {outcome.grade:<8} "
                f"{outcome.relevance:>6.3f} {outcome.rerank_status:<17} "
                f"{outcome.sub_queries:>2} {outcome.citations:>4} {outcome.latency_s:>5.1f}  "
                f"{outcome.answer[:70]}"
            )

    await llm.aclose()
    await reranker.aclose()
    await store.aclose()
    await engine.dispose()

    _summarise(outcomes)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{args.tag}.json"
    path.write_text(
        json.dumps(
            {
                "floor_fused": settings.floor_fused,
                "floor_rerank": settings.floor_rerank,
                "outcomes": [asdict(o) for o in outcomes],
            },
            indent=2,
        )
    )
    print(f"\nwrote {path}")
    return 0


def _floor_sweep(outcomes: list[Outcome]) -> None:
    """What each candidate floor would do, per score source.

    Relevance is already recorded, so re-thresholding it is free — the whole
    sweep is one pass over numbers rather than N eval runs. Reranked and
    un-reranked candidates are swept separately because they are different
    distributions on different scales; averaging them is the bug
    ``applicable_floor`` exists to prevent.

    ``kept`` is answerable questions the floor lets through, ``gated`` is
    should-decline questions it stops. Both rise and fall together, so the number
    to look for is a floor sitting in a *wide* gap between the two populations,
    not the one that maximises the sum — a knife-edge optimum on 55 questions is
    a fit to this corpus, not a threshold.
    """
    for source, label in (
        (("applied", "cached"), "FLOOR_RERANK  (Cohere relevance)"),
        (("skipped_decisive", "failed"), "FLOOR_FUSED   (normalised RRF)"),
    ):
        group = [o for o in outcomes if o.rerank_status in source]
        answerable = [o for o in group if o.expect == "answer"]
        refusals = [o for o in group if o.expect == "decline"]
        if not answerable and not refusals:
            continue

        print(f"\n{label}   n={len(group)}")
        if answerable:
            lo = min(o.relevance for o in answerable)
            print(f"  answerable   min={lo:.3f}  " + _spread(answerable))
        if refusals:
            hi = max(o.relevance for o in refusals)
            print(f"  should-decline max={hi:.3f}  " + _spread(refusals))
        if not (answerable and refusals):
            continue

        print(f"  {'floor':>6} {'kept':>12} {'gated':>14}")
        for floor in [x / 20 for x in range(1, 20)]:
            kept = sum(o.relevance >= floor for o in answerable)
            gated = sum(o.relevance < floor for o in refusals)
            bar = "#" * round(20 * (kept + gated) / (len(answerable) + len(refusals)))
            print(
                f"  {floor:>6.2f} {kept:>6}/{len(answerable):<5} "
                f"{gated:>6}/{len(refusals):<5} {bar}"
            )


def _spread(group: list[Outcome]) -> str:
    values = sorted(o.relevance for o in group)
    return (
        f"p25={values[len(values) // 4]:.3f} "
        f"median={values[len(values) // 2]:.3f} "
        f"p75={values[3 * len(values) // 4]:.3f}"
    )


def _summarise(outcomes: list[Outcome]) -> None:
    if not outcomes:
        return
    answerable = [o for o in outcomes if o.expect == "answer"]
    refusals = [o for o in outcomes if o.expect == "decline"]

    print("\n" + "=" * 60)
    if answerable:
        hits = sum(o.passed for o in answerable)
        wrongly_declined = sum(o.declined for o in answerable)
        print(f"answerable    {hits}/{len(answerable)} correct"
              f"   ({wrongly_declined} wrongly declined)")
    if refusals:
        declined = sum(o.passed for o in refusals)
        print(f"unanswerable  {declined}/{len(refusals)} correctly declined"
              f"   ({len(refusals) - declined} hallucinated)")

    # The split that a single accuracy score hides. Retrieval is scored on
    # whether every document the question needs contributed a chunk to the
    # context the model actually saw; generation is scored only on the questions
    # where it did, because a generator cannot answer from a passage it was
    # never given.
    scoped = [o for o in answerable if o.docs_expected]
    if scoped:
        found = [o for o in scoped if o.retrieved_expected]
        print(f"\nretrieval     {len(found)}/{len(scoped)} had every needed "
              f"document in context")
        if found:
            print(f"generation    {sum(o.passed for o in found)}/{len(found)} "
                  f"correct where retrieval succeeded")
        starved = [o for o in scoped if not o.retrieved_expected]
        if starved:
            for outcome in starved:
                print(f"  retrieval miss  {outcome.id:<5} "
                      f"needed {outcome.docs_expected} got {outcome.docs_cited}")

    # Citation faithfulness. `verify_api.py` proves a citation resolves to real
    # characters; this asks whether those characters back the claim.
    checked = sum(o.facts_checked for o in answerable)
    if checked:
        supported = sum(o.facts_supported for o in answerable)
        claims = sum(o.claims for o in answerable)
        claims_cited = sum(o.claims_cited for o in answerable)
        dangling = sum(o.dangling for o in answerable)
        print(f"\ncitations     {supported}/{checked} stated facts are actually "
              f"present in the passage that claim cites")
        print(f"              {claims_cited}/{claims} factual claims carry any "
              f"citation (G4 coverage)")
        print(f"              {dangling} citation(s) point at no candidate")
        bad = [o for o in answerable if o.facts_supported < o.facts_checked]
        for outcome in bad:
            print(f"  unsupported cite  {outcome.id:<5} "
                  f"{outcome.facts_supported}/{outcome.facts_checked}")

    # The two distributions the floors have to separate. If they overlap, no
    # single threshold can do the job and the problem is upstream of tuning.
    if answerable and refusals:
        good = sorted(o.relevance for o in answerable if o.passed)
        bad = sorted(o.relevance for o in refusals)
        if good and bad:
            print(f"\nrelevance on correct answers   min={good[0]:.3f} "
                  f"median={good[len(good) // 2]:.3f} max={good[-1]:.3f}")
            print(f"relevance on should-decline    min={bad[0]:.3f} "
                  f"median={bad[len(bad) // 2]:.3f} max={bad[-1]:.3f}")

    _floor_sweep(outcomes)

    by_section: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_section.setdefault(outcome.section, []).append(outcome)
    print("\nby section:")
    for section in sorted(by_section):
        group = by_section[section]
        print(f"  {section}  {sum(o.passed for o in group)}/{len(group)}")

    failures = [o for o in outcomes if not o.passed]
    if failures:
        print(f"\nfailures ({len(failures)}):")
        for outcome in failures:
            reason = (
                "answered instead of declining"
                if outcome.expect == "decline"
                else (f"missing {outcome.missing}" if outcome.missing else "declined")
            )
            print(f"  {outcome.id:<5} {reason}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
