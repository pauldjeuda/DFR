# CI validation and production qualification

The pipeline creates a Debian package candidate, installs it in Debian 12, and
then uses that exact package in a three-node Debian 12 cluster. A tagged GitHub
Release is blocked unless the quality, package, and cluster jobs all pass and
the repository declares its license and copyright owner.
Passing these jobs is evidence for the checks below; it is not a blanket claim
that every deployment environment has been qualified.

## Requirement coverage

| Dossier requirement | Automated gate | Coverage status |
|---|---|---|
| F1-F2: detect and replicate file changes | Three-node Bats tests: create, modify, delete, file/directory rename, 1,000-file burst | Covered in container cluster |
| F3: rsync delta for large files | 1 GiB sparse file, 1 MiB edit, actual rsync byte counter checked below 20 MiB | Covered in stress integration |
| F4-F5: multi-master, push and reconciliation | Tests run from each node; offline node reconnects and catches up | Covered for normal disconnection/rejoin |
| F6: LWW, deterministic ties, preserve loser | Concurrent edits with ordered and equal mtimes; conflict row and saved copy checked | Covered |
| F7: operation history | Database-backed log/filter/export unit and API tests | Partially covered; not every daemon action is asserted |
| F8: SSH key access and least privilege | Package checks restricted account shell; cluster uses forced `ssh-gate`; traversal rejected | Covered for tested command paths |
| F9: integrity after replication | SHA-256 checked on each peer for ordinary files and symlink targets | Large-file corruption/retry campaign remains open |
| F10: systemd restart | Installed unit is checked for `Restart=on-failure`; the cluster currently starts the daemon directly | Restart-after-crash is not exercised by CI |
| F11: web status and controls | API checks status, pause/resume and exports | Browser workflow and every route are not end-to-end tested |
| F12: Debian package | Build, inspect, install, executable/config checks and purge in Debian 12 | Covered |
| F13: failure alerts | Offline peer is detected and recovers; notification/alert delivery is not asserted | Partially covered |
| F14: filtered CSV/JSON logs | Unit test covers date/node/type/level filters; API checks both export formats | Covered at API level |
| F15: configure directories and peers in the UI | No configuration screen/API is implemented | Not implemented |

## Remaining release qualification

The dossier also requires measurements or fault injection that the current
container suite does not yet perform: idle CPU and 10,000-file memory limits,
clock skew, corrupted-transfer retries,
disk-full behavior, forced inotify queue overflow, live systemd recovery, and
package upgrade with state preservation. Run these on Debian 12 VMs before
calling a build production-qualified. A CI result must not be described as
"100% production" while any required row above is open.

The repository currently has no `LICENSE` or `packaging/debian/copyright`.
Lintian output is visible during candidate-package builds, but publication of a
tagged Release is blocked until the owner supplies the authorized license and
copyright metadata. The CI must not choose those legal terms on the owner's
behalf.

F15 is classified as a future/optional item in the dossier but remains visible
here so the implementation gap is explicit. The three-node CI is reproducible
locally on a Linux host with Docker after building a `.deb`:

```sh
scripts/build-deb.sh
bash tests/integration/run-docker.sh
```
