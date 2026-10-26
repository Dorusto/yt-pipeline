# D2-15 report — best photo of a burst

## Works
- describe asks the vision model for `photo_flaws` (eyes_closed, blur, motion_blur, tilted, overexposed, underexposed, obstructed, cut_off) and measures `sharpness`; old photo descriptions are redone once; stale photo paths read from `<workspace>/proxy`.
- index: the burst winner has no spoiling flaw, then the best score weighted by relative sharpness; a lone photo with closed eyes or blur is not eligible (tests).
- Full test suite green.

## Mocked / not verified
- The real model's eye judgement on the owner's photos (checked in D2-16 on kotor-demo).

## the owner, check this
- Shots tab after the next run: the left-out photos say `defect: eyes_closed` or `near-duplicate of …`.
