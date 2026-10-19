# CI validation and production qualification

The CI runs unit tests and lint, builds and installs the Debian 12 package, and
uploads the `.deb` as an artifact. A tagged GitHub Release follows the package
job. The three-node container suite is available for manual runs on Linux; it
is not a required CI job. Unit coverage includes the Debian 13 rsync server
command shape and verifies that `ssh-gate` accepts it.

## Requirement coverage

| Dossier requirement | Automated gate | Coverage status |
|---|---|---|
| F1-F2: detect and replicate file changes | Three-node Bats suite: create, modify, delete, rename, 1,000-file burst | Available for manual Docker run |
| F3: rsync delta for large files | 1 GiB sparse file, 1 MiB edit, actual rsync byte counter checked below 20 MiB | Available for manual stress run |
| F4-F5: multi-master, push and reconciliation | Three-node writes and offline peer reconnect | Available for manual Docker run |
| F6: LWW, deterministic ties, preserve loser | Concurrent edits with ordered and equal mtimes; conflict row and saved copy checked | Available for manual Docker run |
| F7: operation history | Database-backed log/filter/export unit and API tests | Unit/API coverage; not every daemon action is asserted |
| F8: SSH key access and least privilege | Package checks restricted account shell; unit test confirms the Debian 13 rsync command with `--partial-dir .dfr/tmp` reaches `ssh-gate`; traversal is rejected in the manual cluster suite | Unit test runs in CI; full SSH flow is manual |
| F9: integrity after replication | SHA-256 checked in the manual cluster suite | Large-file corruption/retry campaign remains open |
| F10: systemd restart | Installed unit is checked for `Restart=on-failure`; live restart is a manual VM test | Restart-after-crash is not exercised by CI |
| F11: web status and controls | API checks local status, fresh SSH peer probes, pause/resume, resync, logs and exports | Unit/API coverage; browser workflow is not end-to-end tested |
| F12: Debian package | Build, inspect, install, executable/config checks and purge in Debian 12 | Covered by CI |
| F13: failure alerts | Offline peer recovery in manual cluster suite | Notification/alert delivery is not asserted |
| F14: filtered CSV/JSON logs | Unit test covers date/node/type/level filters and API exports | Unit/API coverage |
| F15: configure directories and peers in the UI | UI provides CRUD for files/directories under the configured root and add/remove of peers with established SSH trust; changing the global root still requires coordinated node configuration | File and peer APIs covered by unit tests; browser workflow is not end-to-end tested |

## Remaining release qualification

The dossier also requires measurements or fault injection that the current
unit and manual container suites do not perform: idle CPU and 10,000-file
memory limits, clock skew, corrupted-transfer retries, disk-full behavior,
forced inotify queue overflow, live systemd recovery, and package upgrade with
state preservation. Run these on Debian 12 VMs before calling a build
production-qualified. A CI result must not be described as "100% production"
while any required row above is open.

The repository currently has no `LICENSE` or `packaging/debian/copyright`.
Lintian output remains visible during candidate-package builds. The pipeline
does not choose legal terms on the owner's behalf.

F15 is classified as a future/optional item in the dossier. The three-node CI is reproducible
locally on a Linux host with Docker after building a `.deb`:

```sh
scripts/build-deb.sh
bash tests/integration/run-docker.sh
```
