# Render evaluation results — 2026-11-16

Finalized 2026-11-17 (Asia/Shanghai). Evaluation code: PR #57, commit `a2b97027eec546cac257a1cae82b29535b6e5471`; lexicon v14, 686 entries. Machine: Apple M4 Max, 36 GiB unified memory; 251 GiB disk available before the larger-model downloads.

The initial process check found no evaluation running and `ollama ps` was empty. Both 32B result files had already completed: production at 2026-11-16 22:23 CST and eager at 23:03 CST. Running the existing resumable `venv/bin/python continue_qwen32.py` confirmed completion and retained all reply and prompt hashes; it submitted no new inference requests. Each 32B file has 95 unique test replies. The production prompt log contains one additional record for `para-24`, with only one scored reply.

## Combined results

All eight local configurations use the same 95 held-out cases, temperature 0, the PR #57 production prompt, and explicit 16,384-token copies. Eager adds the same frozen worked examples used for 14B. Codex uses the earlier step 2 prompt and saved replies, re-scored under PR #57; it was not rerun with the new production prompt.

`by_kind` is **none / open / paraphrase / protected / synonym**, as passed/total. The denominators are 2 / 11 / 15 / 8 / 59. `phrases_per_case` is the number of valid marked phrases in passing replies divided by all 95 cases. The 95% ranges are Wilson intervals.

| Model / prompt | Pass rate | 95% range | phrases_per_case | by_kind | Median seconds | Median seconds, 10 longest | Peak memory |
|---|---:|---:|---:|---|---:|---:|---:|
| Qwen 2.5 7B / production | 29/95 (30.5%) | 22.2%–40.4% | 0.32 | 0/2 / 4/11 / 3/15 / 5/8 / 17/59 | 7.07 | 7.25 | 5.25 GiB |
| Qwen 2.5 7B / eager | 35/95 (36.8%) | 27.8%–46.9% | 0.36 | 0/2 / 3/11 / 5/15 / 3/8 / 24/59 | 7.51 | 7.68 | 5.25 GiB |
| Llama 3.1 8B / production | 20/95 (21.1%) | 14.1%–30.3% | 0.14 | 1/2 / 7/11 / 2/15 / 4/8 / 6/59 | 7.77 | 7.94 | 6.56 GiB |
| Llama 3.1 8B / eager | 40/95 (42.1%) | 32.7%–52.2% | 0.39 | 1/2 / 5/11 / 2/15 / 4/8 / 28/59 | 8.26 | 8.51 | 6.56 GiB |
| Qwen 2.5 14B / production | 36/95 (37.9%) | 28.8%–47.9% | 0.38 | 0/2 / 3/11 / 5/15 / 2/8 / 26/59 | 14.86 | 15.20 | 11.23 GiB |
| Qwen 2.5 14B / eager | 43/95 (45.3%) | 35.6%–55.3% | 0.48 | 0/2 / 3/11 / 5/15 / 2/8 / 33/59 | 15.71 | 16.23 | 11.23 GiB |
| Qwen 2.5 32B / production | 54/95 (56.8%) | 46.8%–66.3% | 0.51 | 2/2 / 8/11 / 4/15 / 4/8 / 36/59 | 34.04 | 36.92 | 22.29 GiB |
| Qwen 2.5 32B / eager | 56/95 (58.9%) | 48.9%–68.3% | 0.65 | 2/2 / 3/11 / 5/15 / 2/8 / 44/59 | 36.01 | 38.70 | 22.29 GiB |
| Codex gpt-6.1-sol / low / step 2 reference, rescored | 81/95 (85.3%) | 76.8%–91.0% | 0.80 | 2/2 / 11/11 / 13/15 / 8/8 / 47/59 | 16.71 | 18.38 | not measured |

Local requests were serial. Latency includes cold loading and prompt caching; Codex includes submission, queueing and polling. The ten longest cases are selected by original answer character count: `code-01`, `syn-monetization`, `syn-reproducible`, `syn-scandal`, `syn-gerund`, `syn-outperform`, `syn-capable`, `syn-curfew`, `code-12`, `code-09`. These inputs are only 104–164 characters, so this is not a long-document benchmark.

Peak memory is the maximum loaded-model allocation reported by `/api/ps`, sampled alongside `ollama ps` every five seconds across each model’s runs. It is not total system RAM or training memory. All sampled local contexts were 16,384 tokens. The 32B samples show `23 GB`, `100% GPU`, context `16384`, runner `llamacpp`; the API peak is 23,930,978,958 bytes (22.29 GiB). Its copy uses exactly `FROM qwen2.5:32b` and `PARAMETER num_ctx 16384`, matching the other copies’ construction.

Memory-pressure samples (normal / warning / critical): 7B **193 / 0 / 0**; 8B **204 / 1 / 0**; 14B **395 / 0 / 0**; 32B **25 / 910 / 2**. The 32B run experienced sustained warning pressure and two critical samples. Swap was already in use before the sequence; occupancy grew from 2,960.06 MiB initially to 6,174.81 MiB at the last original sample. These observations do not isolate the evaluation from other applications.

## Step 2 scores under the old rules

These are the original scores and original prompts at `54e0e6e`, preserved unchanged. They must be distinguished from the new-policy table above: PR #57 changes the matcher and treats most previous no-phrase cases as open cases. There were no step 2 runs for 14B or 32B.

| Model / old prompt | Original pass rate | Original 95% range | Median seconds |
|---|---:|---:|---:|
| Qwen 2.5 7B / production | 23/95 (24.2%) | 16.7%–33.7% | 7.11 |
| Qwen 2.5 7B / fewshot | 45/95 (47.4%) | 37.6%–57.3% | 7.49 |
| Llama 3.1 8B / production | 20/95 (21.1%) | 14.1%–30.3% | 7.78 |
| Llama 3.1 8B / fewshot | 33/95 (34.7%) | 25.9%–44.7% | 8.19 |
| Codex gpt-6.1-sol / low / production | 78/95 (82.1%) | 73.2%–88.5% | 16.71 |

## Three example failures per model

Replies below are verbatim. Check names are the supplied evaluator’s `markers`, `expected`, `protected`, and `unchanged`. Some expected failures reflect a different valid lexicon choice: for example, `syn-meantime` specifically expects `meantime`, so a valid `meanwhile` marker still fails that case.

### Qwen 2.5 7B

- `none-12`, eager; failed **markers**.

````text
The PR {{touches|changes}} two files.
````

- `syn-rim`, eager; failed **unchanged, expected**.

````text
The rim of the cup was chipped.
````

- `code-11`, production; failed **markers, protected**.

````text
Call `render(text, surface="chat")` and read the {{used|used}} field.
````

### Llama 3.1 8B

- `syn-finite`, eager; failed **markers, expected**.

````text
The universe may be {{finite|bounded}} in size.
````

- `syn-rim`, eager; failed **unchanged, expected**.

````text
The rim of the cup was chipped.
````

- `code-13`, production; failed **protected**.

````text
The regex is {{map-a-to-b|map A to B}} and it must not change.
````

### Qwen 2.5 14B

- `none-12`, eager; failed **markers**.

````text
The PR {{change|changes}} two files.
````

- `syn-meantime`, eager; failed **expected**.

````text
{{meanwhile|Meanwhile}}, let's have some tea.
````

- `code-03`, eager; failed **protected**.

````text
The helper is defined as:

```js
function {{handle-the-heavy-lifting|handle the heavy lifting}}(job) {
  return queue.push(job);
}
```
````

### Qwen 2.5 32B

- `none-07`, eager; failed **markers**.

````text
The invoice total is $9.99 {{per-month|per month}}.
````

- `syn-divisor`, eager; failed **expected**.

````text
Six is a {{denominator|denominator}} of twelve.
````

- `code-11`, eager; failed **markers, protected**.

````text
Call `render(text, surface="chat")` and read the {{used|used}} field.
````

### Codex gpt-6.1-sol / low

- `para-12`, reference; failed **markers, expected**.

````text
Once the PR is merged, I'll {{hand-off-to|hand off}} the remaining work {{hand-off-to|to}} Codex.
````

- `para-26`, reference; failed **markers, expected**.

````text
I {{i-wouldnt-go-that-far|wouldn't go that far}} as to call it a total failure; it mostly worked.
````

- `syn-square`, reference; failed **expected**.

````text
The table is a perfect quadrilateral.
````

## Recommendation for step 3

**Take Qwen 2.5 14B with the frozen eager prompt into fine-tuning.** It raises pass rate from 37.9% to 45.3% and phrase exposure from 0.38 to 0.48 per case, at a 15.71 s median and 11.23 GiB model allocation. It produced more passing phrase exposure than either smaller local model while all 395 of its memory-pressure samples were normal.

32B eager has the highest local score, 58.9%, and exposure, 0.65 per case. Its 36.01 s median, 22.29 GiB allocation, and observed memory pressure make 14B the more practical starting point on this 36 GiB Mac. Keep 32B eager as a local quality reference. The 14B and 32B confidence intervals overlap, so treat the quality gap as an estimate. Training memory has not been measured; inference allocation does not establish that a training configuration will fit.

Focus training examples on correct marker IDs and phrase spans, preserving code exactly, and the weak paraphrase cases (14B eager passed 5/15). It remains important to keep these 95 test cases out of training; use dev cases and separate training examples. The automated checks do not independently establish grammatical or semantic correctness.

## Reproduction, validation, and publication

The existing wrapper is `step2b_run.py`; 32B completion uses `continue_qwen32.py`. Both call the same `run` helper with the test split, OpenAI-compatible base URL `http://127.0.0.1:11434/v1`, API key `ollama`, temperature 0, and a 600 s request timeout. The output stems are `qwen32_production_test` and `qwen32_eager_test`, exactly parallel to the 14B stems.

Frozen eager template: `fewshot_prompt_eager.txt`, SHA-256 `4090dc64db2dc50458580ce5fd033031107dace488dd77420716862c7643d170`. Candidate A was selected using only a 19-case dev pilot, excluding the demonstration cases; its three worked examples derive from dev cases `para-02`, `para-03`, `para-15`, `para-21`, and `para-34`. No prompt tuning was performed in this finishing task.

The completion audit verified all 760 local replies, all current scores and summaries, and every saved production/eager prompt against the unchanged production builder and frozen eager template. All eight local runs have 95 unique test replies and no saved inference API errors. The old template-validation artifact recorded zero comparisons; `completion_validation_summary.json` contains the completed audit. The reference’s two earlier HTTP 405 submissions were recovered in step 2; all 95 reference replies are present.

The combined metrics are in `step2b/combined_summary.json`; memory, pressure, longest-case IDs, and representative `ollama ps` output are in `step2b/execution_summary.json`. The corrected reference summary restores saved timings, which the scorer CLI discards. Original replies, prompt logs, old-rule summaries, and the full original step 2 report remain local.

Publication contains only this report, `artifact_index.md`, and summary JSON files under `eval/render/results/` on branch `eval/results-2026-11-16`. Per-case JSONLs and large prompt/runtime logs remain local. The [artifact index](artifact_index.md) lists every local result path and the corresponding published summaries. No PR is opened.

The live service, launchd agents, Codex configuration, gateway, Ollama global settings, and original local main were not changed. Nothing was restarted, and Downloads was not opened.
