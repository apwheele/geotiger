# Durham demo unmatched review

The current cached Durham run has 135,088 input records. It returned 116,649
automatic matches, 10 review records, and 18,429 unmatched records. Every one
of the 18,429 unmatched records had **zero blocked candidates**, so these are
reference-coverage or input-shape failures, not records that received a weak
fuzzy score.

Preparation now adds 7,622 explicit intersection points from the crossing
street geometries. The input parser identified 6,746 intersection records;
4,402 matched those prepared points and 2,344 remained unmatched.

The diagnostic categories below overlap:

| Diagnostic | Records |
| --- | ---: |
| Unmatched rows with zero blocked candidates | 18,429 |
| Input rows identified as intersections | 6,746 |
| Intersection inputs matched to prepared points | 4,402 |
| Intersection inputs still unmatched | 2,344 |
| House number is `0` | 1,397 |
| House number is missing | 98 |
| Source address contains `/` (likely an intersection) | 2,364 |
| Non-intersection street exists in prepared TIGER data | 6,505 |
| Non-intersection street exists, but no prepared house is within +/-25 | 6,505 |
| No exact non-intersection street exists in prepared TIGER data | 9,580 |

The largest street labels among the unmatched records are `NC 55 HWY` (2,211),
`NC 54 HWY E` (1,209), `FAYETTEVILLE ST` (1,091), `N ROXBORO ST` (817), and
`MT MORIAH RD` (545). The public crime table includes route/intersection
strings and some zero-number locations; these do not correspond to a single
address-range candidate.

### `1450 SNOWCREST TRL` — resolved

This row was a normalization/blocking edge case, not a missing-county problem.
The crime table reports `1400 SNOWCREST TRL`; the demo intentionally changes a
public 100-block label to its analytic midpoint, `1450 SNOWCREST TRL`. The
Durham TIGER range names the same road `Snow Crest Trl` (with a space). The
Durham County boundary contains the official Snowcrest point; similarly named
roads in Orange and Wake are separate `Snowcrest Ln` features.

Previously, the Durham demo used exact normalized-street matching with broad
street fallback disabled. That meant the shared compact street-block key was
never reached for `SNOWCREST` versus `SNOW CREST`; the parser also did not
recognize the abbreviated `TRL` suffix. Exact-street blocking now accepts
spacing-only variants and the normalizer recognizes `TRL`, so the row matches
the TIGER interpolation at `1450` (approximately `35.960135, -78.964847`). This is still an analytic
midpoint of a public block label, not proof that `1450` is an authoritative
situs address; the local address-point service has points such as `1411`
Snowcrest but no exact `1450` point.

There is still a range-coverage problem for ordinary address records. The crime source reports an obfuscated
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
2. Prefer explicit intersection points for intersection-form inputs. This
   demo derives them from TIGER geometry; an authoritative local intersection
   table can supplement or replace those points where bridge/tunnel grade
   separation matters.
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
