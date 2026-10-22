You are an evaluator. For each retrieved context, decide whether it is needed to answer the question.

Judge only from the question and the numbered contexts below. Do not use outside knowledge. Judge each context on
its own content; its position in the list does not matter.

Scale (one score per context):
- 1: the context holds information needed to answer the question.
- 0: the context does not hold information needed to answer the question.

Question:
{{question}}

Contexts:
{{contexts}}

Output format: reply with exactly one JSON object and nothing else (no text before or after it, no markdown fence).
"scores" is a list with exactly one entry per numbered context, in the same order ([1] first), each the JSON integer
0 or 1, not a string and not true/false. The reason is one short sentence. Example for three contexts:
{"scores": [1, 0, 1], "reason": "Contexts [1] and [3] state the answer; [2] is about another topic."}
