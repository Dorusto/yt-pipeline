# RELEASES — released versions and what is stable

One section per version. Per version: the tools and behaviour marked **stable** (passed the real test, has a regression test) and **experimental** (everything else). The protocol is in [`RELEASING.md`](RELEASING.md).

## v0.1.0 (candidate, not released)

**Candidate until the owner confirms the real test.** Nothing here is tagged yet.

**Stable candidate** — the holiday flow in the web app:

- Upload with progress (drag and drop several clips, per-file progress).
- The recap run from the web app.
- The run notice with step times and model cost.
- Versions (each run keeps its edit and renders under `versions/vNNN/`).

**Experimental** — everything else:

- The publish kit.
- Metadata.
- Thumbnail.
- Shorts.
- The audit changes.
- The shaky review policy.
