# Isolated historical Batiseñal / identity composition

Draft review only. No runtime registration, production data, credential, permission,
merge or deployment is included or authorized. **Canon unchanged**: test-only snapshot
composition does not change any product authority or accepted deployment.

The historical source ZIP is byte-identical to the verified recovery package:
SHA-256 b8d89c6d0b1c7f04b48cce0de6e54827740ac1e70c53efa657ed094a21f46b63.
Its own verifier checks the 586 source files before any overlay. Four adapter
modules are unmodified from 76e89a4; overlay-hashes.json pins their bytes and the
new test. Nothing is copied into the repository's production src tree.

The test observes A's actual HTTP identity alongside the binding adapter with
EMPTY dataset ceilings; case_authority remains unchanged. It checks no actor
selection from JSON and revocation after operation/audit before delivery.
B's existing signed WEB session is denied; this is NOT authenticated-B MCP
coverage. No new B MCP grant is fabricated. Multiuser MCP concurrency and actual
Chatty E2E remain open.

CI uses no secrets, persistent credentials or external application endpoints.
The PostgreSQL cluster is NEW, non-root, Unix-socket-only in a private directory,
with host auth rejected and TCP disabled. The ephemeral role agent is solely
required by the original synthetic fixture. The original offline_native runner
blocks other Python network connections. The PostgreSQL version is 16 from the
runner, whereas the historical reference used 17.11: record this difference,
never claim exact-environment reproduction. Existing repository gates unchanged.

The full four historical suites plus three new tests must finish without skips.
Five original pilot modules retain the >=85% exact line-coverage gate. A passing
run remains synthetic integration evidence, not production readiness.

Local evidence: source hashes, AST parse and shell syntax passed; separate adapter
suite reproduced 24/24. This HTTP composition was NOT run locally because sandbox
policy denies Unix sockets. Dependency metadata is the historical observed list,
not a guaranteed clean-install lock. CI installation errors must be reported as
such, never hidden or converted into successful skips.

## Modern transport composition (2026-10-31)

The original ZIP and five-file identity overlay remain byte-identical.
prepare.py applies a hash-pinned reviewed patch only inside its fresh temporary
checkout. It adds modern MCP framing, the approved same-origin form referrer
policy, and an accurate hidden-draft message after revocation/expiry.
Three synthetic client requests gain protocol metadata and matching headers;
their bearer source, domain arguments and assertions do not change.

The separate modern contract runner requires 25 ASGI spy tests plus 8 actual
renderer AST tests, with zero errors/skips and >=85% line coverage in each of
framing.py and http.py. It forbids socket connect/bind/DNS/sendto.
The existing 55 PostgreSQL tests and all five >=85% product-module gates remain.
No dataset ceiling, second MCP grant, Events or publication authority is added.

Surface verification: source hashes and 22 offline presentation/framing tests
passed. The 25 ASGI tests could not run there because the permitted PyPI index
failed DNS; the 55 PG tests require Linux/non-root/PG16/private Unix socket.
Those are not reported as executed locally. CI must provide its own evidence.
Canon unchanged: isolated synthetic fixture composition only, without runtime
registration, production deployment or Chatty E2E acceptance.

Patches use zero-context serialization to avoid whitespace-only context lines in
repository diffs. Exact before/after hashes guard application; output bytes match
the reviewed full-context delta recorded in modern-hashes.json.
