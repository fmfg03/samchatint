# CTT pilot: letter-form photo review correction

Date: 2026-10-27. The pilot story and implementation through deployment were
explicitly approved. Admission still means review only: no team, player or
eligibility is created by viewing a photo.

## Issue and behavior

River Plate review `e2179df9-d57c-50a3-983e-5c6025a5b0f7` is linked to pages 5
and 6 of `Oaxaca femenil 103 PT2.pdf`. Its first displayed photo clipped the
face and included an adjacent area; another preview showed an empty cell. The
previous fixed template was applied after content-based normalization and then
accepted any crop with enough contrast, including paper lines.

For `batch_admission` sessions, the review and future capture paths now use the
same in-memory crop resolution. A player's declared physical source page and
printed slot select the cell, including skipped slots. Source-page references,
asset dimensions and SHA-256 must agree. Letter-form proportions are fitted to
visible grid lines; a local OpenCV detector rejects blank or ambiguous face
regions and faces clipped or displaced toward a neighboring row. No face is
identified by that detector: the operator must compare the crop to the source.
If a page, slot, alignment, detector or photo is uncertain, the UI shows a
pending state and no automatic photo artifact is attached. Legacy non-batch
sessions retain their existing photo path.

Source slots are copied only into ephemeral photo inputs from the immutable
batch draft; they are not written into the draft or approved field payload.
Source assets from a prior immutable release are mapped to the current release
copy for display and reading. A copied review page is served only if its hash
matches the stored asset. No stored database path is rewritten.

## Private pilot evidence

The exact 11 admitted sessions and 22 source-page assets were checked in a
read-only production database transaction. A private local comparison of the
original page and candidate previews covered the 178 listed player slots;
no photo or personal field is committed in this repository. The local detector
returns photo proposals, not proof of identity or eligibility:

| Team | Slots | Photo proposals | Apparently empty | Pending |
|---|---:|---:|---:|---:|
| Deportivo Estrellas | 16 | 12 | 0 | 4 |
| El Arenal | 16 | 4 | 5 | 7 |
| F.C. Margaritas | 16 | 7 | 0 | 9 |
| River Plate | 16 | 8 | 6 | 2 |
| C.F. Florecitas | 16 | 1 | 6 | 9 |
| Deportivo Vikingo | 16 | 4 | 0 | 12 |
| Reductores ACEVAC | 16 | 0 | 0 | 16 |
| Nibelito A.C. | 16 | 7 | 3 | 6 |
| Dep. Sta. Martha | 16 | 5 | 0 | 11 |
| Dep. U de Oaxaca | 16 | 6 | 0 | 10 |
| Tlalixcoyan JR | 18 | 17 | 0 | 1 |
| Total | 178 | 71 | 20 | 87 |

River Plate's eight displayed source photos correspond to its visible photo
slots; six cells appear empty and two are conservatively unresolved. Some
pending cells contain real photos that the conservative alignment or face
signal cannot certify. Empty classifications must also be confirmed against
the source; low contrast, skew and crop quality can limit detection. The
operator-facing source page remains available to inspect those cases.

Synthetic regression tests exercise scale, perspective, printed slots with
omissions, source-page/hash mismatches, blank cells, text/contrast without a
face, adjacent or cut faces, detector failure, stale preview removal, matching
preview/capture paths, and copied assets from a prior release. Native source
images are used only in private validation. Human review remains mandatory.

Canon unchanged: the change adjusts derived photo geometry and safely omits
unverifiable images. It does not change minimum roster rules, duplicate-photo
incident recalculation, tournament scope, blocking decisions, business
authority, capture permission, team creation, eligibility or historical data.
