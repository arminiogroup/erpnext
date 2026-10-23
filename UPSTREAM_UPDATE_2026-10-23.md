# Upstream update validation — 23 October 2026

Tested upstream pair:

- Frappe: `2eb5081d5e27f00ec15e7d1683c2d009987419c1`
- ERPNext: `bfcdeface77e20887b6f0f2c6fd0c9d0ac901a82`

Both are `17.0.0-dev`. The update merges upstream history and preserves the published Italian branch ancestry. Upstream can advance after these targets.

## Catalogue review

| App | Current entries | New reviewed keys | Retired prior keys | Previous targets retained unchanged |
| --- | ---: | ---: | ---: | ---: |
| Frappe | 8,920 | 272 | 48 | 8,648 |
| ERPNext | 12,418 | 175 | 10 | 12,243 |
| Total | 21,338 | 447 | 58 | 20,891 |

The PO and regenerated POT natural-key inventories match. No untranslated or fuzzy entries remain. All compiled Italian dictionary values match their PO targets. One ERPNext source key is a literal whitespace token. Eight literal-percent messages have reviewed `no-python-format` annotations; actual brace-format checks remain enabled. Other locale source files match the selected upstream pair.

## Executed validation

- Installed the candidate dependencies with Bench on Python 3.14.8 and Node 24.22.0.
- Built the merged app assets, including Studio, and migrated an isolated restored fictional QA database successfully.
- GNU `msgfmt --check --check-format` passed for both Italian catalogues; compiled dictionary parity passed for all 21,338 entries.
- Focused Python regressions: 50 tests across metadata messages, print-style names, authorization rules, Studio file errors, Payment Request errors and print document titles.
- Party Items JavaScript regressions: 5 tests, including context isolation and unchanged canonical identities.
- Print-format/style Chrome regressions: 123 distinct cases passed; the 48-case style suite was rerun after fixing test-wrapper environment forwarding. Two constructor cases were rerun after test formatting.
- Repository hooks passed for the source conflict resolutions and changed localization/test files. Known pre-existing source whitespace is assessed separately from new changes.
- An Italian ordinary user loaded Stock Balance, received its completed background-report data with connected realtime, and viewed the unchanged draft Sales Order and its Italian HTML print preview.
- The default classic print selector recovered Italian names. The optional combobox selector displayed Italian captions while preserving the canonical selected Print Format ID; this is not complete acceptance of every optional search mode.

The initial staging setup needed a container-IP database grant, persistent shared assets instead of the old image startup symlink, and a port-specific realtime proxy adjustment. These are local staging setup changes; application source does not contain machine configuration or credentials. The original QA instance was preserved.

## Limits

This is catalogue coverage and focused integration validation, not an exhaustive test of every standard module, responsive layout, accessibility path or native PDF renderer. The fictional company still has missing mandatory print-company details, producing an Italian permission-aware notice. Native PDF acceptance and broader printing remain open. Existing compact-header clipping and some accessibility prefixes also remain open.

The local staging setup mounts the tested candidate sources and uses private dependency/data volumes. It is not an immutable production release image. Deployment requires building from the approved final commit pair, backups and the deployment-specific migration/restart procedure in the [update guide](UPDATING_ITALIAN_LOCALIZATION.md).
