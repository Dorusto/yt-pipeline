# D2-13 report — versions, run state, smooth Ken Burns, exact length

## Works
- Moved workspace with stale photo paths: re-edit through the web form rendered both formats (16:9 and 9:16, 15.6 s) on a synthetic copy of kotor-demo; v1 (old recap) and v2 (new) kept, page screenshots at phone width checked.
- Ken Burns: pan test 51 of 99 still frames before, 5 after (eased ends); zoom on the real render has no still frame.
- Failed run banner, progress "N of M steps", zombie pid no longer `running`, exact seconds and "All good shots" reach `recap.py` (unit tests).
- Strip timings now subtract crossfades like the render (15.5 s strip for a 15.6 s video, before 18 s).
- Full test suite: green.

## Mocked / not verified
- Not run on the real kotor-demo on the LXC yet (needs the owner's pull + restart).
- Best photo of a burst (closed eyes, blur) is not in this change.

## the owner, check this
- `git pull`, restart `web`, then Re-edit kotor-demo with Format both: the page shows v1 (old) and v2 (new) chips.
- Watch a photo zoom in the new version: it should glide, not step.
- Try Length with an exact number (e.g. 45).
