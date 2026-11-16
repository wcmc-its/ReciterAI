# Synopsis length: why the prompt asks for 70 and the code accepts 120 (2026-11-16)

## Question

The publication synopsis had a 95-character limit, stated in the prompt and enforced by a three-attempt reinforcement loop. After the move to Sonnet 5.5 the loop was retrying on nearly every paper. Why, and what stops it?

## Answer

- **Sonnet 5.5 cannot hit a character count by instruction.** It lands about 25–30 characters above whatever number the prompt states, with a spread of roughly ±15. Asked for 95, it overran 95 on 100 of 100 first attempts.
- **Rewording does not fix it.** A lower number moves the whole distribution down; nothing narrows it. A word limit, an "aim for 70 to 85" hint and real examples of the right length all failed.
- **The fix is a gap between what is asked and what is accepted.** The prompt asks for 70 characters; `SYNOPSIS_MAX_CHARS` is 120. 97 of 100 first attempts fit, and the loop makes at most one retry.
- **The extra length is content, not padding.** Given more room the model adds the next most useful detail: a second finding or caveat, a number, the study design. The core finding survives at every length tested. Length is therefore a choice about how much detail to keep, not something the papers dictate.

The rule for anyone writing a length-limited prompt for this model: state a target about 40–50 characters under the limit you will enforce, and measure the first-attempt distribution before shipping.

## What production was doing

Production has run Sonnet 5.5 since 2026-11-10 (first `synopsis_model` log line with that id). Over the six days of `/ecs/reciterai-enrichment` logs before the fix:

- 14 of 38 stored synopses exceeded 95 characters; the longest was 116.
- Every one reported `synopsis_ok: true`. A final overrun is stored as is (never truncated, #50), and the daily job only treats a missing synopsis as a failure.
- The retry line logged at INFO, which the task does not emit, so nothing showed the retries. The only trace was the token bill.

The earlier spike on Sonnet 4.6 had measured a 23% first-attempt overrun; the loop was sized for that.

## Measurements

Sample: 100 Academic Articles from 2020 onward that iCite marks as research articles, with an abstract over 300 characters and no existing synopsis, drawn at random with a fixed seed. Model: `us.anthropic.claude-sonnet-5-5` at effort low through `call_with_fallback`. Costs use `config/llm_prices.yaml`.

First attempt only, by what the prompt asks for:

| Prompt asks for | Shortest | Median | Longest | Fit in 95 | Fit in 110 | Fit in 120 | Fit in 130 |
|---|---|---|---|---|---|---|---|
| 95 (old production prompt) | 97 | 121 | 161 | 0 | 16 | 49 | 82 |
| 80 | 85 | 110 | 141 | 9 | 52 | 77 | 95 |
| 70 | 72 | 98.5 | 135 | 37 | 84 | 97 | 99 |
| 60 | 54 | 83 | 113 | 73 | 99 | 100 | 100 |

Other prompt variants, first attempt, share over 95:

| Variant | Over 95 | Median length |
|---|---|---|
| "at most 12 words" | 100 of 100 | 127 |
| "at most 12 words and 80 characters" | 95 of 100 | 122 |
| "hard limit 95; aim for 70 to 85" (with retries) | 37 of 100 still over after three attempts | 93 |
| Eight real synopses as length examples, limit left at 95 | 87 of 100 | 102 |
| Eight examples plus "<= 70 characters" | 30 of 100 | 92 |

End to end, through `generate_synopsis`:

| Design | Calls per paper | Median final length | Over the limit at the end | Cost per paper |
|---|---|---|---|---|
| Ask 95, accept 95, three attempts (before) | 2.81 | 95 | 49 of 100 | $0.0074 |
| Ask 70, accept 120, two attempts (shipped) | 1.03 | 99.5 | 1 of 100 (123 characters) | $0.0022 |
| Three versions in one call (about 85 / 70 / 55), keep the longest within 95 | 1 | 73 | 0 (1 of 100 had no version that fit) | $0.0029 |

The three-version design also removes the retries and holds a hard 95, but the model's "about 85" version usually runs long, so the 70 or 55 version gets picked and synopses come out shorter (median 73). It was not chosen because the shorter text drops detail that is worth keeping.

## What the extra characters carry

The same 100 papers at two lengths, counted with simple pattern matching, so treat the numbers as rough:

| The synopsis contains | Asked for 95 (median 121) | Asked for 60 (median 83) |
|---|---|---|
| A number (sample size, percentage, years) | 51 | 31 |
| A second clause (a caveat or a further finding) | 46 | 24 |
| A named study design or cohort | 19 | 12 |
| Average words | 17.8 | 12.4 |

A typical pair:

- 130 characters: "In 80 biopsied liver masses, most resolved spontaneously or with antibiotics, but 8% later proved malignant, so follow-up matters."
- 55 characters: "Liver pseudotumours often regress, but some hide cancer"

## What shipped

- `SYNOPSIS_SYSTEM` asks for `<= 70 characters`.
- `SYNOPSIS_MAX_CHARS = 120`, and `SYNOPSIS_SCHEMA` maxLength follows it.
- `SYNOPSIS_MAX_ATTEMPTS = 2`.
- The retry line logs at WARNING.
- `test_synopsis_prompt_asks_for_less_than_the_accepted_length` fails if the prompt's number is raised to within 40 characters of the cap.

No backfill was needed: every synopsis stored before the change is within 120 characters, and Scholars stores and renders the synopsis as unbounded text.

## Not done

- `GRANT_SYNOPSIS_SYSTEM` and `TRIAL_SYNOPSIS_SYSTEM` (`pipeline_area_signals/prompts.py`) still ask for 40–95 characters. They share `generate_synopsis`, so they now accept up to 120 with one retry, but they were not measured and most likely still retry often.
- The impact justification has its own length loop and was not examined.
- Haiku 5.5 and Opus 5.5 were not measured for length.

## Method

Ad hoc scripts, not committed. Each variant replaced one phrase in `SYNOPSIS_SYSTEM`, kept `build_synopsis_user_content` unchanged, and made one call per paper. Nothing was written to DynamoDB or MariaDB. The production figures come from `aws logs filter-log-events` on `/ecs/reciterai-enrichment`.
