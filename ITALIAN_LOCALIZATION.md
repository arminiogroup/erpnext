# Italian localization snapshot

Updated snapshot: 23 October 2026.

This fork merges upstream ERPNext `develop` through [`bfcdeface77e`](https://github.com/frappe/erpnext/commit/bfcdeface77e20887b6f0f2c6fd0c9d0ac901a82) with the reviewed Italian localization and still-required contextual source fixes. Use the tested pair of upstream targets: Frappe **2eb5081d5e27f00ec15e7d1683c2d009987419c1**, ERPNext **bfcdeface77e20887b6f0f2c6fd0c9d0ac901a82**. Both identify as **17.0.0-dev**. Upstream may advance after this snapshot.

- [Frappe](https://github.com/arminiogroup/frappe/tree/italian-localization)
- [ERPNext](https://github.com/arminiogroup/erpnext/tree/italian-localization)

This app's regenerated catalogue contains **12,418 entries**, including **175 newly reviewed keys**; 10 keys from the previous snapshot were retired. All 12,243 surviving previous targets are retained unchanged. The paired catalogues contain **21,338 entries**, with no untranslated or fuzzy entries against the matching source templates. This is catalogue coverage; it does not establish complete UI or business-workflow acceptance. One retained ERPNext key is a literal whitespace token.

The update preserves upstream discount validation and adds contextual handling for Party Items counts, payment-error document captions and extensionless Studio file errors. Explicit `no-python-format` annotations identify eight reviewed literal-percent labels/wildcards that Babel otherwise misidentifies as printf expressions; real brace-format checks remain active.

Validation includes an isolated restored fictional QA site, dependency installation, asset build, database migration, GNU gettext checks, focused Python/JavaScript regressions and Chrome checks. See the [update validation record](UPSTREAM_UPDATE_2026-10-23.md) for exact results. Broader standard-module, responsive, accessibility and native PDF/printing acceptance remains open. Known earlier UI limits include English accessibility filter prefixes and clipped compact report headers.

These are independently maintained forks. Upstream source fixes and translation submissions are a separate contribution process; publication here does not imply upstream acceptance. `develop` is reserved for upstream tracking.

For upstream synchronization, translation-conflict handling and deployment checks, see the [update guide](UPDATING_ITALIAN_LOCALIZATION.md).

The upstream [license](license.txt) remains unchanged.
