# Durham demo unmatched review

The current cached Durham run has 135,088 input records and uses one reference
built from Durham, Orange, and Wake County TIGER ranges. It returns 129,110
automatic matches (**95.57%**), 283 review records (0.21%), and 5,695 unmatched
records (4.22%). The unmatched rate is down from 5.91% before 100-block house
retrieval, directional street-name parsing, and expanded USPS suffix
recognition. The prior automatic match rate was about **93.8%**.

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
penalized and generally sent to review. An exact or spacing-only street string
still scores as a perfect street identity when an older prepared table kept a
now-recognized suffix in the name field.

## Match methods

| Candidate method | Matched | Review | Unmatched |
| --- | ---: | ---: | ---: |
| Exact street | 114,649 (84.87%) | 0 | 0 |
| Spacing variant | 795 (0.59%) | 0 | 0 |
| Canonical street/route | 8,211 (6.08%) | 108 (0.08%) | 0 |
| Phonetic street name | 251 (0.19%) | 155 (0.11%) | 1,234 (0.91%) |
| Exact intersection | 4,435 (3.28%) | 0 | 0 |
| Canonical intersection | 759 (0.56%) | 0 | 0 |
| Phonetic intersection | 10 (0.01%) | 20 (0.01%) | 8 (0.01%) |
| No candidate | 0 | 0 | 4,453 (3.30%) |

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

Recent general parser and retrieval changes also recover 100-block range-end
misses such as `1250 BRIAR ROSE LN` (TIGER ends at 1199), directional street
names such as `SOUTH ST`, and recognized suffixes such as `FOXRIDGE CRES`.
The Durham notebook, not the library, maps 100-block labels including the
000-block (`0` -> `50`) before geocoding.

## Remaining unmatched records

The remaining misses partition as follows:

| Cause | Records | Share of misses | Share of inputs |
| --- | ---: | ---: | ---: |
| Ordinary address with zero candidates | 2,931 | 51.47% | 2.17% |
| Intersection unavailable or too weak | 1,526 | 26.80% | 1.13% |
| Ordinary candidate scored below review | 1,233 | 21.65% | 0.91% |
| Zero or missing house number | 5 | 0.09% | 0.00% |

The 5,695 rows represent 1,771 unique raw addresses, so repeated block labels
still dominate row counts.

The largest remaining misses are reference-coverage or numbering issues rather
than suffix or one-letter spelling failures: names absent from TIGER
(`YUNUS RD`, `AMBER SHADOW DR`), interstate mile-marker house numbers far
outside the prepared I-40/I-85 ranges, named highways that are not the TIGER
route string (`WAKE FOREST HWY`), combined routes such as `US 15 501`, and
houses more than one 100-block past the last prepared range.

Of 6,746 intersection inputs, 5,179 now match automatically, 25 go to review,
and 1,542 remain unmatched. An authoritative local centerline/intersection
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
