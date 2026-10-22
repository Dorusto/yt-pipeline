# Task briefs

Written by the architect agent, one file per task: `D<phase>-<nn>-<slug>.md`. Each brief is self-contained, because the coder agent sees nothing else. Kept in git so every change can be traced back to the brief that asked for it.

Launch pattern (agents run without a person to answer prompts): opencode run --auto --agent architect "..." . --auto approves what is not explicitly denied; secrets and ssh keys are denied in ~/.config/opencode/opencode.jsonc (external_directory), and rules there are last-match-wins, so the catch-all goes first.
