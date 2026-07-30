/**
 * Exercise `lib/sse.ts` against a live backend.
 *
 * The SSE parser is the one piece of frontend logic that can corrupt an answer
 * *silently*: mishandle a chunk boundary and a token vanishes, mishandle a
 * keepalive and a `:` line is parsed as data. Neither throws, and both look like
 * a model problem rather than a client bug.
 *
 * This drives the real module against real server output - including the
 * heartbeat frames and whatever chunking the network happens to produce - and
 * asserts the §8 ordering guarantees on what comes back.
 *
 *   pnpm exec tsx scripts/verify-sse.ts
 */

import { streamChat, streamIngest } from "../lib/sse";
import type { Citation } from "../lib/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const CORPUS = `# Ops Runbook

The ZX9-4471 pressure valve regulates coolant flow in the secondary loop.
If it fails, error code E-1180 is raised and the loop must be isolated.

## Escalation

Page the on-call engineer within 15 minutes of an E-1180 alert.
`;

function check(label: string, ok: boolean, detail = ""): boolean {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${detail ? ` - ${detail}` : ""}`);
  return ok;
}

async function main(): Promise<number> {
  let failures = 0;

  // -- upload ---------------------------------------------------------------
  const form = new FormData();
  form.append("file", new File([CORPUS], "sse-probe.md", { type: "text/markdown" }));
  const uploaded = await fetch(`${API}/documents`, { method: "POST", body: form });
  const doc = (await uploaded.json()) as { id: string };
  console.log(`\nuploaded ${doc.id} (HTTP ${uploaded.status})`);

  // -- ingest stream --------------------------------------------------------
  console.log("\ningest stream:");
  const ingestEvents: string[] = [];
  for await (const event of streamIngest(API, doc.id)) {
    ingestEvents.push(event.type);
    if (event.type === "document.complete" || event.type === "document.error") break;
  }
  failures += check(
    "exactly one terminal ingest event",
    ingestEvents.filter((e) => e === "document.complete" || e === "document.error")
      .length === 1,
    ingestEvents.join(" -> "),
  )
    ? 0
    : 1;

  // -- chat stream ----------------------------------------------------------
  console.log("\nchat stream:");
  const seen: string[] = [];
  const seqs: number[] = [];
  let answer = "";
  let citations: Citation[] = [];
  let verificationAfterComplete = false;
  let deltaBeforeGenerateStart = false;
  let generateStarted = false;
  const stageKeys = new Set<string>();

  for await (const event of streamChat(API, {
    message: "What error code does the ZX9-4471 valve raise?",
  })) {
    seen.push(event.type);
    seqs.push(event.data.seq);

    if (event.type === "pipeline.stage") {
      stageKeys.add(`${event.data.node}:${event.data.attempt}:${event.data.state}`);
      if (event.data.node === "generate" && event.data.state === "started") {
        generateStarted = true;
      }
    }
    if (event.type === "answer.delta") {
      if (!generateStarted) deltaBeforeGenerateStart = true;
      answer += event.data.text;
    }
    if (event.type === "answer.complete") citations = event.data.citations;
    if (event.type === "verification.complete") {
      verificationAfterComplete = seen.includes("answer.complete");
    }
  }

  const terminals = seen.filter((e) =>
    ["answer.complete", "abstain", "error"].includes(e),
  );

  const results = [
    check("turn.start is first", seen[0] === "turn.start", seen[0]),
    check("seq is strictly monotonic", seqs.every((s, i) => i === 0 || s > seqs[i - 1])),
    check("exactly one terminal event", terminals.length === 1, terminals.join(",")),
    check("answer.delta only after generate started", !deltaBeforeGenerateStart),
    check("answer text was reassembled", answer.length > 0, answer.slice(0, 70)),
    check("citations returned", citations.length > 0, `${citations.length}`),
    check(
      "citations arrive unverified (null, not false)",
      citations.every((c) => c.verified === null),
      JSON.stringify(citations.map((c) => c.verified)),
    ),
    check(
      "citations carry offsets for the source pane",
      citations.every((c) => Number.isInteger(c.char_start) && c.char_end > c.char_start),
    ),
    check("verification.complete followed answer.complete", verificationAfterComplete),
    check(
      "stage events are distinct per (node, attempt, state)",
      stageKeys.size === seen.filter((e) => e === "pipeline.stage").length,
      `${stageKeys.size} keys`,
    ),
  ];
  failures += results.filter((ok) => !ok).length;

  // -- multi-turn: the brief's stated trap ----------------------------------
  //
  // "Follow-up questions should work" is the requirement most easily faked:
  // each turn looks correct in isolation while the conversation has no memory
  // at all. The test is a pronoun - "it" is meaningless without the prior turn.
  console.log("\nmulti-turn:");
  let conversationId: string | null = null;
  for await (const event of streamChat(API, {
    message: "What does the ZX9-4471 valve do?",
  })) {
    if (event.type === "turn.start") conversationId = event.data.conversation_id;
  }
  failures += check(
    "turn.start reports the conversation id",
    Boolean(conversationId),
    conversationId ?? "(missing)",
  )
    ? 0
    : 1;

  // Checking only the answer text is a false positive waiting to happen: "What
  // error code does it raise?" contains "error code", which BM25 finds in the
  // document on its own - the turn looks like memory working when it is just
  // keyword retrieval. So the assertion is on the *rewrite*, which is the only
  // stage that can resolve "it" and can only do so from conversation history.
  let followUp = "";
  let rewritten = false;
  let threadedConversationId: string | null = null;
  for await (const event of streamChat(API, {
    message: "What error code does it raise?",
    conversation_id: conversationId,
  })) {
    if (event.type === "turn.start") {
      threadedConversationId = event.data.conversation_id;
    }
    if (
      event.type === "pipeline.stage" &&
      event.data.node === "rewrite" &&
      event.data.state === "done"
    ) {
      rewritten = Boolean(event.data.detail?.rewritten);
    }
    if (event.type === "answer.delta") followUp += event.data.text;
  }

  failures += check(
    "follow-up stays in the same conversation",
    threadedConversationId === conversationId,
    `${threadedConversationId} vs ${conversationId}`,
  )
    ? 0
    : 1;
  failures += check(
    "rewrite resolved the pronoun from history",
    rewritten,
    rewritten ? "'it' was substituted" : "no rewrite - memory did not reach the node",
  )
    ? 0
    : 1;
  failures += check(
    "follow-up answered correctly",
    /E-1180/.test(followUp),
    followUp.slice(0, 90),
  )
    ? 0
    : 1;

  // -- cleanup --------------------------------------------------------------
  await fetch(`${API}/documents/${doc.id}`, { method: "DELETE" });

  console.log(
    failures === 0
      ? "\nAll SSE contract checks passed.\n"
      : `\n${failures} check(s) failed.\n`,
  );
  return failures === 0 ? 0 : 1;
}

main().then((code) => process.exit(code));
