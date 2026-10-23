# Case study: a question card that would not close

A worked example of every trap in `SKILL.md`, from the session that produced this
skill. Read it before you decide a fix is impossible.

## The symptom

DSH had a WeChat plugin that mirrored desktop prompts to the phone. When the phone
answered a question, the desktop question card stayed on screen — with a live input
box and a working submit button, not a greyed-out leftover. The desktop could still
answer a question that had already been answered on the phone.

## The mechanism, after four wrong guesses

The desktop card is keyed by a tool-call id. The client's `PendingQuestion`
constructor:

```js
constructor(sessionId, questions, callId, siblings, review) {
    this.callId = callId;
    this.dismissal = callId === void 0 ? "cancel" : "hide";
    if (callId === void 0) nextQuestionKey += 1;
    this.key = callId === void 0
        ? `question:${unnamedQuestionPrefix}:${String(nextQuestionKey)}`
        : PendingQuestion.keyOf(sessionId, callId);
}
```

So a question **without** a call id gets an anonymous, per-process counter key
(`question:anon:1`), while the Session projection records the same question under its
**real** id (`call_00_…`). The card-removal pass compares keys:

```js
for (const card of cards.values()) {
    if (card.pending.callId === void 0 || card.pending.review !== void 0) continue;
    if (recorded.has(card.pending.key)) { card.remove(); continue; }
    if (rows.has(card.pending.key) || card.hasRequest() || card.pending.hasWaterfall()) continue;
    card.remove();
}
```

Two different key spaces, one comparison. `recorded.has()` can never be true for an
anonymous card, and the first line skips anonymous cards entirely. The card survives.

Why the question had no call id: the Host only names one for *timed* questions
(schemas declaring a `timeout` parameter). A plain `ask_user_question` has no
`timeout`, so `request.wait` is undefined and the client falls back to the counter.

## The four failures, and what each taught

| Attempt | Change | Why it failed |
| --- | --- | --- |
| 1 | Hook `answerQuestion`'s `finally` to drop the card | That `finally` never runs: the client-side waterfall promise never settles when the plugin answers first, so the whole block is dead code. |
| 2 | Add a `recorded` set to `reconcile()` | Correct idea, wrong data: the anonymous key still cannot match the real id. |
| 3 | Remove the `!fold.timed` gate so every question enters the projection | Necessary, but not sufficient — and it silently did nothing, because the same function existed in **two** asar entries (`lib/index.js` and `lib/types/projection.js`) and only one was patched. |
| 4 | Patch both copies | Now the projection tracked the question — but the card still had no id to match against. |

Four asar swaps, one of which crashed the app (stale `integrity` hashes). All four
addressed a real defect. None addressed *the* defect.

## The fix (plugin only, one tier up)

The id already existed. The Host emits `tool/call` with `data.callId` before the
question is asked; the plugin simply was not passing it along. So:

1. Listen for `session/event`; on `tool/call` with `name === 'ask_user_question'`,
   queue `data.callId` per session.
2. In `questionListener`, if the request has no `wait`, shift the queue and set
   `record.wait = { callId }`.
3. Let the existing projection settle it; the client's `reconcile()` now matches keys
   and removes the card.

Guards that mattered in review: FIFO order per session, ignore non-question tool
calls, clear the queue at `turn/end` so a stale id cannot attach to a later question,
never overwrite a `wait` the Host already set, and degrade to the old behaviour when
no id is known.

Verified with 8 unit tests against the real patched module (id is stamped, claimed
once, FIFO, per-session isolation, host-provided `wait` preserved, unknown session
still works) plus the 3 pre-existing regression tests. Then confirmed on the device:
phone answered, desktop card disappeared.

## Lessons to carry

- **A fix in the wrong tier can be perfectly correct and still useless.** The code
  was in the asar; the missing value was in the plugin.
- **"I changed the code" is not "the behaviour changed."** Attempt 3 changed code that
  did not run. Verify the live artifact (size, markers, boot log) every time.
- **When several identical functions exist, patch all of them or prove which one
  runs.** `asar.py grep` reports the entry path for exactly this reason.
- **A failing `finally` is a smell.** If the block that should clean up never runs,
  find what is supposed to settle the promise above it.
- **One crashed app is worth ten silent no-ops** for teaching you what to verify.
