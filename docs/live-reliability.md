# Live-model reliability

`scripts/live_replay.py --repeat 3` with Reader `claude-sonnet-5-5` and Writer `claude-sonnet-5-5`: each scenario run 3 times against the real models. A run passes when every turn passes every hard check (state, leaks, guard, model answered); pass^N is 1.00 only when all N runs passed, else 0.00 (pass^k counts a task only when all k trials succeed). Soft misses are wording expectations the live Writer paraphrased.

| Scenario | Turns | Hard passes | pass^3 | Soft misses (run: turn, text) |
|---|---|---|---|---|
| abusive_caller | 4 | 3/3 | 1.00 | none |
| angry_caller | 7 | 3/3 | 1.00 | none |
| casual_identity_phrasing | 2 | 3/3 | 1.00 | none |
| decoy_disambiguation | 2 | 3/3 | 1.00 | none |
| dob_correction | 3 | 3/3 | 1.00 | none |
| document_checklist | 3 | 3/3 | 1.00 | none |
| first_name_only | 2 | 3/3 | 1.00 | none |
| human_request_then_continue | 2 | 3/3 | 1.00 | 1: T1 missing 'available'; 2: T1 missing 'available'; 3: T1 missing 'available' |
| identity_question | 4 | 3/3 | 1.00 | none |
| injection_attempt | 1 | 3/3 | 1.00 | none |
| margaret_happy_path | 6 | 3/3 | 1.00 | none |
| near_miss_phone_then_more | 2 | 3/3 | 1.00 | none |
| no_claims_on_file | 2 | 3/3 | 1.00 | none |
| off_topic_three_times | 4 | 3/3 | 1.00 | none |
| question_after_goodbye | 4 | 3/3 | 1.00 | none |
| refusing_caller | 2 | 3/3 | 1.00 | none |
| representative_after_policyholder | 5 | 3/3 | 1.00 | none |
| representative_approved | 4 | 3/3 | 1.00 | none |
| representative_declared | 2 | 3/3 | 1.00 | none |
| representative_timeout | 8 | 3/3 | 1.00 | none |
| reverify_as_another_party | 5 | 3/3 | 1.00 | none |
| reverify_then_own_handoff | 7 | 3/3 | 1.00 | none |
| spanish_caller | 2 | 3/3 | 1.00 | none |

Overall: 69/69 scenario runs passed every hard check (100%).
