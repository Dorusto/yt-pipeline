# D2-14 report — best shots first, the model's edit repaired, Shots tab marks

## Works
- Rule director, chronological: heroes / shots >= 0.85 take up to 40 % of the slots first; a three-hero take now keeps all three (test), static framings of one take still never sit back to back (test).
- LLM director: a draft with mechanical violations is repaired and kept (out-of-window cut rebuilt, slot outside the plan dropped, weaker shot of a jump-cut pair dropped); only an unrepairable draft falls back. `meta.director` says who made the edit and why.
- `explain` runs after every new edit; it names the director and marks an unused plan. A mock recap shows `llm_director … fallback` in the log and "rule-based director" in Why?.
- Shots tab: every card marked "in the edit as vN" or "left out: reason", filters All / In the edit / Good but left out (phone screenshot checked).
- Full test suite green.

## Mocked / not verified
- Not run with the real model on kotor-demo yet: whether the repaired draft keeps 0072/s5, s7b1/s7b5 needs a real re-edit.
- Thresholds (40 %, 0.85, 8 s) are first guesses.

## Doru, check this
- `git pull`, restart `web`, Re-edit kotor-demo (60 s): Why? starts with "Edited by the model" or the fallback reason.
- Shots tab → "Good but left out": the list should be short and not contain the tunnel heroes.
