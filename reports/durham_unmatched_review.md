# Durham demo unmatched review

The current cached Durham run has 135,088 input records. It returns 126,061
automatic matches, 302 review records, and 8,725 unmatched records. The
unmatched rate is **6.46%**, down from 13.64% before component-aware street
matching.

The parser retains street name, suffix, and directional components. Candidate
blocking now tries, in order:

1. exact normalized street;
2. spacing-only equivalence;
3. canonical street-name and route identity;
4. a phonetic street-name key; and
5. the optional broad street-signature fallback.

The Durham demo keeps the broadest fallback disabled. Common variants can
therefore enter scoring without a street-name cross join. The street score is
80% name, 4% suffix, and 16% directional. A road-type disagreement such as
`RD`/`DR` is soft evidence, while an opposite `N`/`S` direction is materially
penalized and generally sent to review.

## Match methods

| Candidate method | Matched | Review | Unmatched |
| --- | ---: | ---: | ---: |
| Exact street | 111,517 | 10 | 0 |
| Spacing variant | 726 | 4 | 0 |
| Canonical street/route | 8,372 | 220 | 0 |
| Phonetic street name | 290 | 44 | 767 |
| Exact intersection | 4,402 | 0 | 0 |
| Canonical intersection | 746 | 0 | 0 |
| Phonetic intersection | 8 | 24 | 3 |
| No candidate | 0 | 0 | 7,955 |

The method is retained in `match_method`, and component scores are retained in
`score_street_name`, `score_street_suffix`, `score_directional`,
`score_pre_directional`, and `score_post_directional` for analyst review.

High-volume recovered variants include:

| Crime-data form | TIGER form | Newly matched rows |
| --- | --- | ---: |
| `NC 55 HWY` | `STATE HWY 55` | 2,195 |
| `NC 54 HWY E` | `E STATE HWY 54` or `STATE HWY 54` | 1,202 |
| `FAYETTEVILLE ST` | `FAYETTEVILLE RD` | 1,072 |
| `N ROXBORO ST` | `ROXBORO RD` or `N ROXBORO RD` | 782 |
| `MT MORIAH RD` | `MOUNT MORIAH RD` | 544 |
| `HARDEE ST` | `N HARDEE ST` | 465 |
| `NINTH ST` | `9TH ST` | 278 |
| `IVEY WOOD LN` | `IVY WOOD LN` | 157 |
| `SEDWICK RD` | `SEDWICK DR` | 149 |

`NINTH`, `9NTH`, and `9TH` share the canonical `9TH` identity. `MOUNT` and
`MT` share one lexical identity. Numbered state, US, and Interstate route forms
use the supplied state plus route number. `IVEY` and `IVY` remain distinct,
auditable parsed names but share a phonetic candidate key.

## Remaining unmatched records

The remaining misses partition as follows:

| Cause | Records | Share of misses |
| --- | ---: | ---: |
| Ordinary address with zero candidates | 5,297 | 60.71% |
| Intersection unavailable or too weak | 1,566 | 17.95% |
| Zero or missing house number | 1,254 | 14.37% |
| Ordinary candidate scored below review | 608 | 6.97% |

The 8,725 rows represent 2,469 unique raw addresses, so repeated block labels
still dominate row counts. Examples include `1250 BRIAR ROSE LN`, whose
nearest prepared range ends at 1199, and new/private or source-specific names
such as `YUNUS RD`. These are reference-coverage or range-window issues rather
than suffix or one-letter spelling failures.

Of 6,746 intersection inputs, 5,156 now match automatically, 24 go to review,
and 1,566 remain unmatched. An authoritative local centerline/intersection
table can improve the remainder where TIGER geometry is disconnected or where
an interchange is not a true at-grade crossing.

`1450 SNOWCREST TRL` remains resolved through spacing-equivalent matching to
TIGER's `SNOW CREST TRL`. It is an analytic midpoint of the public 1400-block
label, not an assertion that 1450 is an authoritative situs address.

Recommended analyst treatment for the remainder is to keep weak records in
review/unmatched status, supplement TIGER with local address points or parcels,
and use explicit lookup aliases for agency-specific names that are not safe as
national normalization rules.

The full diagnostic is embedded in
[`notebooks/durham_demo.ipynb`](../notebooks/durham_demo.ipynb).
