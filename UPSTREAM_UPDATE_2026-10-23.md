# Upstream update validation — 23 October 2026

Tested upstream pair:

- Frappe: `82ea5b3ad52584e29b264ccc35acd4ed55955edc`
- ERPNext: `118df8914580e45ca0c7783e9ca27c12f80de8ed`

Both are `17.0.0-dev`. The update merges upstream history and preserves the published Italian branch ancestry. Upstream can advance after these targets.

## Catalogue review

| App | Current entries | New reviewed keys | Retired prior keys | Previous targets retained unchanged |
| --- | ---: | ---: | ---: | ---: |
| Frappe | 9,380 | 732 | 48 | 8,648 |
| ERPNext | 12,417 | 208 | 44 | 12,209 |
| Total | 21,797 | 940 | 92 | 20,857 |

The PO and regenerated POT natural-key inventories match. No untranslated or fuzzy entries remain. All compiled Italian dictionary values match their PO targets. One ERPNext source key is a literal whitespace token. Eight literal-percent messages have reviewed `no-python-format` annotations; actual brace-format checks remain enabled. Other locale source files match the selected upstream pair.

## Executed validation

- Installed the candidate dependencies with Bench on Python 3.14.8 and Node 24.22.0.
- Built the merged app assets, including Studio and the newly imported Flow module, and migrated an isolated restored fictional QA database successfully.
- GNU `msgfmt --check --check-format` passed for both Italian catalogues; compiled dictionary parity passed for all 21,797 entries.
- Focused Python regressions: 50 tests across metadata messages, print-style names, authorization rules, Studio file errors, Payment Request errors and print document titles.
- Party Items JavaScript regressions: 5 tests, including context isolation and unchanged canonical identities.
- Print-format/style Chrome regressions: 123 distinct cases passed; the 48-case style suite was rerun after fixing test-wrapper environment forwarding. Two constructor cases were rerun after test formatting.
- Repository hooks passed for the source conflict resolutions and changed localization/test files. Known pre-existing source whitespace is assessed separately from new changes.
- An Italian ordinary user loaded Stock Balance, received its completed background-report data with connected realtime, and viewed the unchanged draft Sales Order and its Italian HTML print preview.
- The default classic print selector recovered Italian names. The optional combobox selector displayed Italian captions while preserving the canonical selected Print Format ID; this is not complete acceptance of every optional search mode.
- After the final upstream merge, Flow's 61 frontend tests and 18 daemon lifecycle tests passed, along with the two browse-command tests. The exact new development-URL test body passed as the ordinary QA user. Flow's daemon reported healthy in the persistent staging backend.
- The ordinary Italian user's Stock Balance and unchanged draft Sales Order were checked again after the final migration, with realtime connected.

The 50 Python, 5 Party Items and 123 Chrome cases above ran at the first integration checkpoint. Their localization implementations were retained; the changed URL helper branch was tested separately. These checkpoint suites were not all rerun. A broader attempt to load the upstream financial-formula suites stopped during their unconditional fixture bootstrap because the restored site already contained `Standard Buying`. Those suites are not reported as passing. A separate finite probe exercised the real formula validator, dependency resolver and row processor without that fixture bootstrap.

The staging setup needed a container-IP database grant, persistent shared assets instead of the old image startup symlink, a port-specific realtime proxy adjustment, and `procps` for Flow's process lifecycle. These are local staging setup changes; application source does not contain machine configuration or credentials. The original QA instance was preserved.

## Limits

This is catalogue coverage and focused integration validation, not an exhaustive test of every standard module, responsive layout, accessibility path or native PDF renderer. The fictional company still has missing mandatory print-company details, producing an Italian permission-aware notice. Native PDF acceptance and broader printing remain open. Existing compact-header clipping and some accessibility prefixes also remain open.

Flow's new standalone frontend still has unmarked English text and no translation dictionary bootstrap. For example, its startup and setup screens contain “Flow could not start” and “Finish setup to start”. Translating every extracted catalogue key does not translate those screens. Flow metadata and marked backend/launcher messages are covered by the Italian catalogue; the standalone frontend requires a separate source localization change.

The selected ERPNext upstream target introduces a formula dependency edge case: in `sum([A for A in [1]]) + A`, the comprehension's local `A` incorrectly hides the outer reference from dependency analysis. An actual finite probe confirmed that a dependent row placed before calculated row `A` can fail with `NameError`, and related undefined/self-reference checks can be missed. The prior upstream validator detected these outer references. This update preserves the selected upstream implementation and records the defect; financial formulas using comprehensions need separate correction and regression validation before production use. A suspected recursion-error escape did not reproduce with 1,000–10,000 terms.

The local staging setup mounts the tested candidate sources and uses private dependency/data volumes. It is not an immutable production release image. Deployment requires building from the approved final commit pair, backups and the deployment-specific migration/restart procedure in the [update guide](UPDATING_ITALIAN_LOCALIZATION.md).
