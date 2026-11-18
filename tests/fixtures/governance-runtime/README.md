# Exact governance runtime test fixture

The Python source and schema files are byte-for-byte copies of the approved
Zaubern candidate, identified individually in manifest.json. The manifest
records the repository, inspected base and observed main commits; candidate
changes are identified by their content hashes, without pretending they were
already part of either commit. This snapshot is test input, not a production
fork, configuration, policy approval, credential, or service launcher.

Only the CI bootstrap places this fixture on its isolated test PYTHONPATH.
Production packaging and service assembly never import from this directory.
Refresh the manifest and rerun acceptance after any approved source update.

Exact original Zaubern test helper modules are also retained byte-for-byte for
channel protocol tests. Their paths are explicitly listed in test_helper_files;
they are neither operational authority nor a modified fork of their fixtures.

The V3 batch helper is also retained byte-for-byte from the separately pinned
candidate. It exercises synthetic source/proof/finality bytes and grants no
production authority. The fixture manifest identifies the uncommitted candidate
by content hashes; it does not claim those changes belong to an older commit.
