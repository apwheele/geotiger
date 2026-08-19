# Durham demo unmatched review

The current cached Durham run has 135,088 input records. It returned 111,517
automatic matches, 10 review records, and 23,561 unmatched records. Every one
of the 23,561 unmatched records had **zero blocked candidates**, so these are
reference-coverage or input-shape failures, not records that received a weak
fuzzy score.

The diagnostic categories below overlap:

| Diagnostic | Records |
| --- | ---: |
| Unmatched rows with zero blocked candidates | 23,561 |
| House number is `0` | 1,476 |
| House number is missing | 130 |
| Source address contains `/` (likely an intersection) | 6,766 |
| Exact normalized street exists in prepared TIGER data | 6,506 |
| Exact street exists, but no prepared house is within +/-25 | 6,506 |
| No exact normalized street exists in prepared TIGER data | 17,055 |

The largest street labels among the unmatched records are `NC 55 HWY` (2,211),
`NC 54 HWY E` (1,209), `FAYETTEVILLE ST` (1,091), `N ROXBORO ST` (817), and
`MT MORIAH RD` (545). The public crime table includes route/intersection
strings and some zero-number locations; these do not correspond to a single
address-range candidate.

There is also a range-coverage problem. The crime source reports an obfuscated
100-block address and the demo moves it to the block midpoint (`*50`). For the
6,506 unmatched rows whose normalized street does exist in the local TIGER
expansion, none has a prepared TIGER house number within the current +/-25
house-number window. A wider diagnostic window found 2,384 within +/-100 and
5,524 within +/-1,000, indicating that some streets/ranges are present but do
not line up tightly enough with the public block label. Other streets—especially
route aliases and intersection strings—are absent under the exact normalized
street key.

Recommended analyst treatment:

1. Keep the unmatched records as `unmatched`; do not silently assign a broad
   street-block candidate to an intersection or route label.
2. Handle intersections with a separate intersection/reference table or split
   the two street names and retain an explicit intersection match method.
3. Treat `0` and missing house numbers as segment- or street-level records,
   rather than residential address points.
4. Add locally authoritative address points or parcels for route aliases and
   high-value streets. The combined reference table will take precedence over
   TIGER ranges for equal scores.
5. If block-midpoint inputs are acceptable for the analysis, evaluate a wider
   house-number tolerance as a separate sensitivity run and review those
   assignments; changing the production default would reduce locality safety.

The full diagnostic is embedded in
[`notebooks/durham_demo.ipynb`](../notebooks/durham_demo.ipynb).
