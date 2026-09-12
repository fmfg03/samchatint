# Direction executive dashboard: source and gap analysis

Date: 2026-09-12
Base: `090188c5e35e9b0733cdb4fa325db46331f4cdfc`
Target runtime: `copa_telmex_dashboard.py` / `samchat-gastos.service`
Target surface: `/direccion/tableros`
Evidence state: repository worktree only; not deployed or business accepted

## Source boundary

The first implementation slice reuses two existing scoped read models:

- `samchat.budgets.service.build_budget_snapshot` for tournament budget totals;
- `samchat.tournaments_v2.services.build_tournament_soul_snapshot` plus
  `samchat.sports_platform.director_general_dossier` for operations, teams,
  players, managers, matches, standings and media.

It does not query global Finance, CxC, cashflow or Payment Run data. Missing
facts remain visibly missing. The page performs no business write.

## Field inventory

| Area | Requested fact | Current source | Current coverage | Gap / required owner |
| --- | --- | --- | --- | --- |
| Entity operations | Entity name | Supabase `teams.state` through tournament SOUL | Available, but state is being used as the entity dimension | Confirm that state is the canonical entity identity or create an explicit entity catalog |
| Entity operations | Plataforma Sports owner | None in the scoped snapshot | Missing | Explicit entity-to-employee assignment |
| Entity operations | Entity contact name, phone and email | Supabase `team_managers` | Partial and potentially repeated by team | Canonical entity contact identity and deduplication rule |
| Entity operations | Contact birth date | None in `team_managers` snapshot | Missing | Sensitive contact-profile field and access/audit policy |
| Entity operations | Partner name and birth date | None | Missing | Sensitive related-person record, purpose and retention policy |
| Entity operations | Expected teams by category/gender | No planning source | Missing | Tournament/entity participation plan |
| Entity operations | Real teams by category/gender | `teams`, `registrations`, `categories.branch` | Available | Confirm data freshness and branch normalization |
| Entity operations | Players by category, age and gender | `players.birth_date`, registration category and `categories.branch` | Available as aggregate after this slice | Gender is category branch, not player-level sex; label must remain explicit |
| Entity operations | Teams advancing each round | `matches.phase`, scores and `team_standings` | Partially derivable | Canonical round-advancement/finality rule |
| Entity operations | State-phase organization, referee fees and transport | None structured | Missing | State-phase operating brief and cost fields/evidence |
| Entity operations | National qualifiers | Matches/standings may suggest candidates | Not authoritative | Explicit qualification decision/state |
| Entity operations | State uniform delivery date/place | None | Missing | Logistics delivery event and evidence |
| Entity operations | National outbound/return travel | None | Missing | Trip itinerary linked to entity/team |
| Entity operations | Final place per team | `team_standings` | Partial | Final-table marker and category/phase finality |
| Entity finance | First and successive operator transfers | `documentos`/Payment Run contain payment facts | Not safely attributable to entity | Entity/operator beneficiary mapping plus paid-evidence query |
| Entity finance | Uniforms, balls and equipment cost | `expense_reports` concepts and `team_id` | Partially derivable | Canonical equipment taxonomy and entity attribution |
| Entity finance | Visit reports | Documents/attachments have generic evidence | Missing semantic relationship | Visit record linked to responsible employee, entity and report |
| Entity finance | Expense per visit | Expense records exist | Not safely attributable to one visit | Visit-to-expense relationship |
| National operations | Tournament/category/dates/duration | Tournament/category records | Partial | City, inauguration/closing semantics and confirmed final dates |
| National operations | Hotels and bed-nights | Hotel fiscal fields exist on expenses | Cost evidence only | Contracted hotel inventory, rooms/beds/nights |
| National operations | Meals by service type | Expense concepts may contain food | Not reliable | Catering order/contract quantities by meal type |
| National operations | Sports complex | No canonical venue record in current snapshot | Missing | Venue catalog and event assignment |
| National operations | Number/type of fields | `matches.field_number` | Partial identifiers only | Field catalog with type and venue |
| National operations | Medical service description | No structured source | Missing | Medical coverage plan, provider and evidence |
| National operations | Accidents requiring transfer | No authoritative incident source | Missing | Restricted incident record; exclude unnecessary clinical detail |
| National finance | PS staff travel costs | Expenses by tournament/phase/employee | Partially derivable | Approved travel taxonomy and national-phase scope |
| National finance | Hotel advances/liquidations by service | Documents, expenses and payment evidence | Partially derivable | Contract/service allocation and hotel supplier mapping |
| National finance | Other supplier payments | Documents and Payment Run | Partially derivable | National-final purpose/category rule |
| National finance | Medical costs | Expenses and suppliers | Partially derivable | Medical taxonomy and event relationship |
| National finance | Insurance cost | Expenses and suppliers | Partially derivable | Insurance taxonomy and policy/evidence relationship |
| Marketing | Activation suppliers present | No attendance relationship | Missing | Activation event and supplier attendance |
| Marketing | Sponsor visitors | No visitor register | Missing | Sponsor/visitor/event relationship and privacy policy |
| Marketing | Activity report, outcome and photos | Gallery media exists | Evidence count only | Activation report linked to sponsor, result and media assets |

## Implemented first slice

- replaces the two-KPI HTML with a responsive, high-contrast executive dossier;
- preserves position, portfolio and tournament authorization from #319;
- loads operations only for each authorized tournament;
- displays entity managers, real teams and player aggregates;
- adds player age aggregation without exposing individual birth dates;
- exposes explicit missing-state language instead of rendering absent money or
  operational facts as zero;
- includes structured sections for entity operations, entity Finance, national
  phase and Marketing;
- preserves read-only behavior and legacy GET redirects.

## Required next slices

1. Establish the canonical entity identity and PS owner assignment.
2. Define expected participation and final round/qualification records.
3. Build an entity-scoped Finance bridge over canonical payment evidence.
4. Add national logistics records for travel, hotel, food, venues and medical
   coverage.
5. Add activation reports linking suppliers, sponsor visitors, outcomes and
   media evidence.
6. Add authenticated UAT with the five configured Direction holders.

## Canon impact

The explicitly approved Direction amendment grants eligible Direction positions
cross-domain, read-only visibility into Finance and operational facts inside
their assigned active portfolio and tournament scope. It does not grant create,
modify, approve, pay, publish or delete authority; specific denials prevail.
The approved canon changes and their verified hashes are committed in this PR.
