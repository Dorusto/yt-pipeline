# Notebook merge prompt

The learning notebook of one instance of a video-editing pipeline is full: it
holds as many lessons as it may. You read the current lessons and make room by
merging overlapping ones or removing stale ones, without losing what they teach.
A human reads the notebook and can delete any lesson.

## Inputs

The tool appends one section after this prompt:

- `## Lessons (N of LIMIT)` — one `- <id>: <text>` line per lesson in the
  notebook.

## Output

Return EXACTLY one JSON object and nothing else:

```json
{
  "merge": [
    {"ids": ["l001", "l002"], "text": "the merged lesson, one short sentence"}
  ],
  "remove": ["l003"]
}
```

Field rules:

- `merge` — each entry names at least 2 ids from the list and one `text` that
  keeps what those lessons teach in one short sentence. The named lessons are
  replaced by the new one.
- `remove` — the ids of lessons that are stale or superseded and can go with no
  replacement.
- Use empty lists when nothing overlaps and nothing is stale.

## Rules

- Only ids that appear in `## Lessons`.
- Never invent a lesson the current ones do not support.
- Write in the same language as the lessons.
- One sentence per merged lesson; no personal data.
