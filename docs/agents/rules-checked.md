# Rules checked by tests

One row per bullet in [`docs/RULES.md`](../RULES.md), in RULES order, grouped by
RULES section. `Checked by` names the test file that checks the rule, or `review`
for the rules that depend on review alone (no program can check them).

| Rule | Checked by |
|:---|:---|
| Editing grammar — analysis tools label, never delete | `tests/test_quality.py` |
| Editing grammar — geometry skip roles never become slots | `tests/test_director.py` |
| Editing grammar — a move and its landing form one phrase | `tests/test_director.py` |
| Editing grammar — phrase window and landing floor | `tests/test_director.py` |
| Editing grammar — phrase score is the move's score | `tests/test_director.py` |
| Editing grammar — same-take slots need a different take between | `tests/test_director.py` |
| Editing grammar — one candidate contributes at most one slot | `tests/test_director.py` |
| Editing grammar — no two similar segments in one edit | `tests/test_director.py` |
| Editing grammar — hero and semantic moves keep their range | `tests/test_director.py` |
| Editing grammar — length policy and speed ramp | `tests/test_moves.py` |
| Editing grammar — on-screen length formula | `tests/test_moves.py` |
| Editing grammar — an edit never misses an eligible hero | `tests/test_edit_golden.py` |
| Editing grammar — important-shot cap and peak-centred cuts | `tests/test_director.py` |
| Editing grammar — peak-centred cuts | `tests/test_peak.py` |
| Editing grammar — ordinary slot length from the pacing | `tests/test_pacing.py` |
| Editing grammar — pacing table | `tests/test_pacing.py` |
| Editing grammar — time-overlapping shots keep the higher score | `tests/test_overlap.py` |
| Editing grammar — long segments split into content beats | `tests/test_describe.py` |
| Editing grammar — the screenwriter's menu is a shortlist of the cuttable pool | `tests/test_llm_director.py` |
| Editing grammar — edit.json is the only source of truth | `tests/test_schema.py` |
| Editing grammar — Mode B paired slots | `tests/test_export_filmcraft.py` |
| Editing grammar — Mode B cut rules | `tests/test_rough_cut.py` |
| Editing grammar — cut_review.yaml phone review | `tests/test_rough_cut.py` |
| Editing grammar — chapters.json chapter source of truth | `tests/test_chapters.py` |
| Editing grammar — shorts_candidates.json ranked Shorts | `tests/test_shorts_rank.py` |
| Editing grammar — directors record a reason per candidate | `tests/test_decisions.py` |
| Editing grammar — edit.json records the music track | `tests/test_decisions.py` |
| Editing grammar — router is deterministic | `tests/test_router.py` |
| Editing grammar — LLM director loop and fallback | `tests/test_llm_director.py` |
| Editing grammar — new geometric rules only when a case recurs | review |
| Editing grammar — shot length changed from the web app | `tests/test_edit_drop.py` |
| Defaults and thresholds — motion floors | `tests/test_motion.py` |
| Defaults and thresholds — parallax and truck/orbit relabels | `tests/test_motion.py` |
| Defaults and thresholds — platform_bob is an annotation | `tests/test_motion.py` |
| Defaults and thresholds — horizon detection | `tests/test_quality.py` |
| Defaults and thresholds — scene activity band and events | `tests/test_motion.py` |
| Defaults and thresholds — accidental run rule | `tests/test_quality.py` |
| Defaults and thresholds — quality segments head/tail and slivers | `tests/test_quality.py` |
| Defaults and thresholds — shotlog score formula | `tests/test_index.py` |
| Defaults and thresholds — confident skip roles are ineligible | `tests/test_index.py` |
| Defaults and thresholds — similarity backend and threshold | `tests/test_similarity.py` |
| Defaults and thresholds — eval scoring | `tests/test_eval_labels.py` |
| Defaults and thresholds — describe frames and beats | `tests/test_describe.py` |
| Defaults and thresholds — render preview and final | `tests/test_render.py` |
| Model routing — architect and coder models | review |
| Model routing — vision default and backend | `tests/test_vision.py` |
| Model routing — local Qwen for private footage | review |
| Model routing — every LLM call through common/llm.py | `tests/test_llm.py` |
| Model routing — transcriber interface | `tests/test_transcribe.py` |
| Model routing — config.yaml model ids and budget | `tests/test_config.py` |
| Model routing — music models YuE2 and ACE-Step | review |
| Model routing — no ComfyUI | review |
| Model routing — no torch unless needed; encoder choice | `tests/test_ffmpeg_tools.py` |
| Tool exit codes — one exit-code table for every tool | `tests/test_tool_contract.py` |
| Tool exit codes — EXIT_USAGE versus EXIT_RUNTIME | `tests/test_thumbnail_concepts.py` |
| Tool exit codes — a --json failure is one error object | `tests/test_costs.py` |
| Costs — ledger path three ways | `tests/test_costs.py` |
| Costs — import-donations CSV | `tests/test_costs.py` |
| Costs — upload low-credit warning | `tests/test_serve.py` |
| Costs — instance limits and shared project costs | `tests/test_serve.py` |
| Costs — costs page read-only | `tests/test_serve.py` |
| Costs — feedback.jsonl and requests file separate | `tests/test_feedback.py` |
| Costs — LLM director reviewer verdict | `tests/test_llm_director.py` |
| Safety rules — originals read-only | `tests/test_render.py` |
| Safety rules — private by default, tailnet-only | `tests/test_serve.py` |
| Safety rules — never commit footage, keys or private IPs | `tests/test_check_public.py` |
| Safety rules — CI runs the suite on ubuntu | review |
| Safety rules — feedback is append-only JSON lines | `tests/test_feedback.py` |
| Safety rules — every --json summary reports wall time and cost | `tests/test_summary.py` |
| Safety rules — watchdog thresholds and no-commit clock | `tests/test_agent_watchdog.py` |
| Safety rules — skills change with their scripts | `tests/test_docs_in_step.py` |
| Safety rules — short docstrings allowed, no inline comments | review |
| Safety rules — Short word timestamps from the timeline words | `tests/test_subs_from_edit.py` |
| Safety rules — corrections capitalised variant | `tests/test_correct_srt.py` |
| Safety rules — publish.py verify step | `tests/test_publish.py` |
| Safety rules — manual-upload kit is its own tool | `tests/test_publish_kit.py` |
| Safety rules — reframe moving crop path | `tests/test_reframe.py` |
| Safety rules — every tool standalone with --help and --json | `tests/test_tool_contract.py` |
| Safety rules — no blocking prompt | `tests/test_shorts_generator.py` |
| Safety rules — tool-to-tool subprocess calls limited | review |
| Safety rules — no tool depends on one person's setup | `tests/test_no_personal_setup.py` |
| Safety rules — metadata prompt fixed | `tests/test_metadata.py` |
| Safety rules — channel.fixed mapping | `tests/test_config.py`, `tests/test_metadata.py`, `tests/test_doctor.py` |
| Safety rules — channel.style mapping | `tests/test_config.py`, `tests/test_metadata.py`, `tests/test_recap_meta.py` |
| Safety rules — learning notebook and the learn pass | `tests/test_notebook.py` |
| Safety rules — --skip-alignment only when the JSON is correct | review |
| Safety rules — recap.py is the single entry point | `tests/test_recap.py` |
| Safety rules — recap skip rule | `tests/test_recap.py` |
| Safety rules — proxies keep the source filename | `tests/test_proxy.py` |
| Safety rules — preview services tailnet-only | `tests/test_serve.py` |
| Safety rules — recap.py --notify on success only | `tests/test_recap.py` |
| Safety rules — proxies keep the source metadata | `tests/test_proxy.py` |
| Safety rules — inbox grouping | `tests/test_inbox.py` |
| Safety rules — inbox.py waits for the inbox to be quiet | `tests/test_inbox.py` |
| Safety rules — phone flow | `tests/test_serve.py` |
| Safety rules — web upload endpoint streams into the inbox | `tests/test_serve.py` |
| Safety rules — excluded.json lists left-out files | `tests/test_excluded.py` |
| Safety rules — brief_facts.json holds the four facts | `tests/test_serve.py` |
| Safety rules — web app look is one token set | `tests/test_serve.py` |
| Safety rules — recap.py thumbs station | `tests/test_thumbs.py` |
| Safety rules — shotlog grouping is index.py's | `tests/test_index.py` |
| Safety rules — colour.py session colour recipe | `tests/test_colour.py` |
| Safety rules — director selection and stars | `tests/test_director.py` |
| Safety rules — top heroes kept by the model | `tests/test_llm_director.py` |
| Safety rules — edit expectations are tests | `tests/test_eval_edit.py` |
| Safety rules — versions | `tests/test_versions.py` |
| Safety rules — vertical 9:16 | `tests/test_vertical.py` |
| Safety rules — README is the front door | `tests/test_readme_layout.py` |
| Safety rules — AGENTS.md is the shared instruction file | `tests/test_agents_md.py` |
| Safety rules — typed MCP server per plugin | `tests/test_yt_mcp.py` |
| Photos and music — photos are first-class candidates | `tests/test_photos.py` |
| Photos and music — photo pseudo-segment p1 | `tests/test_describe.py` |
| Photos and music — burst rule | `tests/test_index.py` |
| Photos and music — photo slots and Ken Burns | `tests/test_edit_golden.py` |
| Photos and music — Ken Burns renders with zoompan | `tests/test_render.py` |
| Photos and music — music is the bed under the ambient | `tests/test_render.py` |
| Photos and music — --music auto picks the track | `tests/test_music_pick.py` |
| Photos and music — beat-snapped cuts | `tests/test_llm_director.py`, `tests/test_director.py` |
| Packaging and brand — brand.json is the brand kit | `tests/test_thumbnail.py` |
| Packaging and brand — fonts load only from the brand paths | `tests/test_thumbnail.py` |
| Packaging and brand — thumbnail cut-out is rembg | `tests/test_thumbnail.py` |
| Packaging and brand — thumbnail tests use synthetic portraits | `tests/test_thumbnail.py` |
| Packaging and brand — app logo is one of three SVG marks | `tests/test_serve.py` |
| Releasing — stable tools change only in a named lot | review |
| Releasing — stable-files check | `tests/test_check_stable.py` |
| Releasing — continuous deployment | `tests/test_deploy.py` |
| Releasing — deploy/ holds deployment scripts | `tests/test_deploy.py` |
| Releasing — Dependabot | `tests/test_dependabot.py` |
