# Updating the Italian localization forks

This guide maintains the paired ArminioGroup forks:

- [Frappe](https://github.com/arminiogroup/frappe), branch **italian-localization**
- [ERPNext](https://github.com/arminiogroup/erpnext), branch **italian-localization**

Update and test the two apps together. Stop on failed commands, except the expected merge-conflict state explained below. The instructions are a maintenance procedure; publishing this document does not upgrade either application or the running test site.

## What “2 commits ahead, 2849 commits behind” means

GitHub compares our branch with upstream **develop**. “Ahead” counts our commits; “behind” counts upstream commits after the common ancestor, including merged branch histories. It does not count missing translations or releases. A small number of our commits can contain many source and translation changes.

The original published snapshot uses Frappe base **faea3728fcd14b7d3f1a2d9f5b3259bf3ef94734** and ERPNext base **6de47690f780e41e463652d54bf5010100b59521**. Those bases and upstream develop checked on 23 October 2026 identify themselves as **17.0.0-dev**. The same development version label does not establish dependency or API compatibility.

The branches have distinct jobs:

| Branch | Purpose |
| --- | --- |
| upstream/develop | Latest official upstream code fetched locally. |
| origin/develop | An upstream-tracking branch in our fork. |
| origin/italian-localization | Our published source fixes, Italian catalogue and matching template. |
| update/italian-UPDATE-ID | A candidate merge, reviewed and tested before promotion. |

Use a **merge** for the shared Italian branch, preserving its published history. Avoid rebasing or force-pushing it. GitHub’s **Sync fork** acts on the selected branch; do not use **Discard commits** on italian-localization, because that removes our work.

## 1. Prepare an isolated staging bench and its app checkouts

This procedure assumes a disposable staging bench with both forks installed. Prepare it using the [official setup documentation](https://docs.frappe.io/framework/user/en/installation) or your custom-image development tooling first; this guide does not bootstrap that environment.

**The maintenance checkouts must be the staging bench's actual apps/frappe and apps/erpnext working trees.** Resolve the candidate directly there so that extraction, builds and tests use the exact edited files. Cloning or installing an uncommitted candidate into a different bench would omit those edits and test the wrong code.

From the staging bench root, inspect the remotes:

~~~bash
git -C apps/frappe remote -v
git -C apps/erpnext remote -v
~~~

Origin must point to the corresponding ArminioGroup fork. If it still points to the official repository, change the origin URL:

~~~bash
git -C apps/frappe remote set-url origin git@github.com:arminiogroup/frappe.git
git -C apps/erpnext remote set-url origin git@github.com:arminiogroup/erpnext.git
~~~

Add the official remote only if upstream is missing; verify its URL instead if it already exists:

~~~bash
git -C apps/frappe remote add upstream https://github.com/frappe/frappe.git
git -C apps/erpnext remote add upstream https://github.com/frappe/erpnext.git
~~~

Use **one terminal per app** for sections 2–4 and 7, in apps/frappe and apps/erpnext respectively; this preserves each app's shell variables and merge state. Use another terminal at their common **bench root** for sections 5–6. A standalone Git clone is not a bench.

Use a disposable test site or a restored staging copy with outbound email and integrations controlled. Record the two previously deployed commit/image IDs, and back up its database and public/private files before testing migrations. Keep production on the previous tested pair while preparing the candidate.

## 2. Fetch, inspect the gap and choose a fixed target

Run in each repository:

~~~bash
git status --short
git fetch --no-prune origin
git fetch --no-prune upstream
git switch italian-localization
git merge --ff-only origin/italian-localization
git rev-list --left-right --count HEAD...upstream/develop
git log --first-parent --oneline HEAD..upstream/develop
git diff --stat HEAD...upstream/develop
~~~

Start with a clean working tree. Save or commit unrelated work separately before proceeding. The count command prints **our ahead count first, upstream’s behind count second**. Read the upstream changes and migration notes, especially where they overlap our fixes.

Choose the fetched upstream commit in each app and record the pair. Do not chase a moving tip during validation. Recheck the candidate’s Python, Node, database and package requirements; the old environment may need rebuilding.

Optionally refresh **only the fork’s develop tracking branch**, after fetching:

~~~bash
git merge-base --is-ancestor origin/develop upstream/develop &&
git push origin upstream/develop:refs/heads/develop
~~~

A nonzero ancestry check needs investigation. Do not add a force flag. Updating develop alone does not update italian-localization.

## 3. Create update branches and preserve the reviewed Italian files

Use the same update ID in both repositories, with a new ID for each attempt. Set the app name to **erpnext** when working in that repository.

~~~bash
localization_app=frappe
localization_update_id=2026-10-23
localization_previous_sha=$(git rev-parse HEAD)
localization_upstream_sha=$(git rev-parse upstream/develop)
localization_po_backup=$(mktemp -d)
printf '%s\n' "$localization_po_backup"

git branch "backup/italian-$localization_update_id" "$localization_previous_sha"
git switch -c "update/italian-$localization_update_id"
git show "HEAD:$localization_app/locale/it.po" > "$localization_po_backup/italian-before.po"
git show "$localization_upstream_sha:$localization_app/locale/it.po" > "$localization_po_backup/italian-upstream.po"
git show "HEAD:$localization_app/locale/main.pot" > "$localization_po_backup/template-before.pot"
git rev-parse HEAD upstream/develop
git merge --no-ff --no-commit "$localization_upstream_sha"
~~~

Keep the printed SHAs and backup directory location in the update notes. Preserve the PO copies beyond temporary-directory cleanup if the work spans sessions.

The last command intentionally leaves a merge uncommitted. It may return a conflict error; resolve that state before continuing. The published branch is still untouched.

## 4. Resolve the cause of conflicts

List unresolved paths with:

~~~bash
git diff --name-only --diff-filter=U
git status
~~~

For source conflicts, read both versions and their callers. Keep upstream behaviour together with any still-required localization fix. If upstream fixed the original cause or replaced the component, adapt or retire our old patch instead of restoring the old implementation.

Review extraction configuration, whole-sentence messages, placeholders, contexts, shared Frappe/ERPNext keys, generated labels and print/report consumers. Do not resolve every conflict with a blanket “ours” or “theirs” strategy.

For **DocType JSON**, follow the repository’s UI/export workflow in the staging environment. Preserve newly added upstream fields and their properties, then reapply required local metadata changes through the DocType editor and review its exports. A schema conflict may need its own preparation; do not hand-edit metadata or drop upstream fields simply to complete the merge.

For **main.pot**, regenerate it from the resolved source; it is a generated template.

For an **Italian PO conflict**, preserve the two saved copies. Our reviewed PO can be used as a temporary working seed while synchronizing with the new template. Review upstream Italian additions too; choosing the seed is not a decision to discard their work. If needed, these commands restore just the working copies of the generated files, leaving the merge index unresolved:

~~~bash
git restore --source=HEAD --worktree -- "$localization_app/locale/it.po"
git restore --source=HEAD --worktree -- "$localization_app/locale/main.pot"
~~~

Resolve all non-catalogue conflicts and make the candidate source usable before extraction. Translation identity includes **app, context, source message and plural source**. Matching English text alone is insufficient when context or meaning changed.

## 5. Regenerate and review the Italian catalogues in staging

The candidate working trees are already the staging bench's apps/frappe and apps/erpnext repositories from section 1. Use the dependencies required by that candidate. Do not install fresh copies of their old HEADs into another bench. From this **staging bench root**:

~~~bash
bench setup requirements
bench generate-pot-file --app frappe
bench generate-pot-file --app erpnext
bench update-po-files --app frappe --locale it
bench update-po-files --app erpnext --locale it
~~~

Generate Frappe first: ERPNext can exclude messages already supplied by Frappe’s template. Specify **--locale it** so this maintenance step does not rewrite every language.

The current PO updater preserves exact matches and disables fuzzy matching. New or changed keys still need contextual translation and review. Compare the result with both saved PO copies; preserve good reviewed translations, assess new upstream wording, and review changed consumers even when a source key stayed the same. Do not blindly copy old Italian targets into a changed context or clear fuzzy flags without reviewing the text.

Do not carry the original **20,949 entries / 100%** claim into a new source inventory automatically. Recalculate coverage against the newly generated templates.

## 6. Validate translations, code and the running candidate

From the staging bench root, run GNU gettext checks and compile the actual runtime catalogues:

~~~bash
msgfmt --check --check-format --statistics -o /dev/null apps/frappe/frappe/locale/it.po
msgfmt --check --check-format --statistics -o /dev/null apps/erpnext/erpnext/locale/it.po
bench compile-po-to-mo --app frappe --locale it --force
bench compile-po-to-mo --app erpnext --locale it --force
~~~

Compilation alone does not prove complete coverage. The following read-only check requires **polib** in the staging Python environment; install it there if necessary with **./env/bin/python -m pip install polib**. Polib is an optional maintenance-review tool, not a new application runtime dependency; do not add it to the app requirements for this check. Run this from the bench root:

~~~bash
./env/bin/python - <<'PY'
from pathlib import Path
import polib

def identity(entry):
    return (entry.msgctxt or "", entry.msgid, entry.msgid_plural or "")

total = 0
for app in ("frappe", "erpnext"):
    directory = Path("apps") / app / app / "locale"
    po = polib.pofile(str(directory / "it.po"))
    pot = polib.pofile(str(directory / "main.pot"))
    entries = [e for e in po if e.msgid and not e.obsolete]
    sources = [e for e in pot if e.msgid and not e.obsolete]
    keys = [identity(e) for e in entries]
    source_keys = [identity(e) for e in sources]
    assert len(keys) == len(set(keys)), f"{app}: duplicate PO keys"
    assert len(source_keys) == len(set(source_keys)), f"{app}: duplicate POT keys"
    assert set(keys) == set(source_keys), f"{app}: PO/POT inventory mismatch"
    for entry in entries:
        assert "fuzzy" not in entry.flags, f"{app}: fuzzy {entry.msgid!r}"
        if entry.msgid_plural:
            assert set(entry.msgstr_plural) == {0, 1}, f"{app}: plural forms"
            assert all(entry.msgstr_plural.values()), f"{app}: blank plural"
        else:
            assert entry.msgstr, f"{app}: blank {entry.msgid!r}"
    total += len(entries)
    print(f"{app}: {len(entries)} complete, non-fuzzy entries")
print(f"Total: {total}; linguistic and runtime review still required")
PY
~~~

Regeneration can infer printf directives from literal percent text. This snapshot records eight reviewed `no-python-format` annotations: the Onboarding wildcard, the Item Tax Template percentage description, three cumulative-progress labels, Task progress validation, the BOM process-loss message and the project completion percentage. Check their actual consumers and preserve the literal-percent annotations in both PO and POT; retain genuine brace/printf checks. Do not clear format flags to hide a real mismatch.

These checks do not replace linguistic review or prove semantic placeholder/markup correctness. Check placeholders and rendered markup against the source, run the affected localization regressions and repository-required checks, and review any new format-check failures rather than suppressing them.

Test the **pair** on a staging site. From its bench root, replacing the example site name:

~~~bash
bench build
bench --site staging.example.test migrate
bench --site staging.example.test clear-cache
~~~

Migration changes the staging database. Test build/dependency changes, background workers and realtime delivery, representative normal-user workflows, reports, dialogs, and HTML/PDF printing. Include the stock balance/ledger and manufacturing/accounting wording we changed. Inspect Italian wrapping and accessibility labels. A successful merge or green syntax check is not a runtime acceptance result.

## 7. Commit, review and promote the tested pair

Return to each app's existing terminal. If you opened a new terminal, set localization_app to frappe or erpnext and localization_update_id to the same update ID again before running these commands. Stage the deliberately resolved source files and the two generated catalogue files. Do not stage database dumps, site configuration, private/public user files, credentials, dependencies, built assets or compiled MOs.

~~~bash
git add -- "$localization_app/locale/main.pot" "$localization_app/locale/it.po"
git diff --name-only --diff-filter=U
git diff --cached --check
git diff --cached --stat
git status
~~~

The unresolved-path list must be empty. Review the full staged diff, including resolved source files. Resolve or explicitly assess whitespace diagnostics; do not claim a check passed when it did not.

~~~bash
git commit -m "chore(i18n): merge upstream develop and refresh Italian catalogue"
git status --short
git rev-parse HEAD
~~~

Both candidate working trees must be clean. From the staging bench root, record the final commit pair:

~~~bash
git -C apps/frappe status --short
git -C apps/erpnext status --short
git -C apps/frappe rev-parse HEAD
git -C apps/erpnext rev-parse HEAD
~~~

The status commands must produce no changes. Validation must apply to these exact committed bytes. If commit hooks, UI exports or review edits changed files after the earlier checks, repeat the affected validation before promotion. Then, from each app's terminal:

~~~bash
git push -u origin "update/italian-$localization_update_id"
~~~

Publish candidate branches for review in the **ArminioGroup forks**, with the exact Frappe/ERPNext SHAs, upstream targets, fresh catalogue counts, actual tests and migration/UI results. Opening an upstream PR is a separate contribution step.

After both candidates pass review and staging validation, promote each branch using a fast-forward:

~~~bash
git fetch --no-prune origin
git switch italian-localization
git merge --ff-only origin/italian-localization
git merge --ff-only "update/italian-$localization_update_id"
git push origin italian-localization
~~~

If either fast-forward fails because someone advanced the public branch, incorporate that work into the candidate and repeat the affected validation. Do not force-push. Two repository pushes are not atomic: deploy only after the complete tested pair is available.

The “behind” count should reach zero against the chosen upstream targets. Upstream may advance again while testing; that does not invalidate the tested commit pair.

## 8. Deploy and recover separately

Updating the forks does not update a running site. Record the approved two commit SHAs and build new deployment images from that pair. For Docker, replace app services with those images using the deployment’s normal procedure; editing a running container is not a durable update. Keep the previous images and tested database/file backups.

For an existing site, perform the deployment’s backups, maintenance/downtime procedure and required migrations, asset/translation compilation, cache clearing and service restarts, then repeat the acceptance checks. The exact Docker/Bench commands depend on that deployment.

Before a candidate merge is committed, **git merge --abort** abandons it. After committing, keep the candidate branch for diagnosis and leave the published branch on the previous tested version until fixed. If a deployed migration must be rolled back, restore the matching database/files together with the prior app/image pair; rolling back Git alone does not undo database changes. Preserve any business writes made after the backup before planning restoration.

## References

- [Snapshot and compatibility notes](ITALIAN_LOCALIZATION.md)
- [Frappe gettext CLI](https://github.com/frappe/frappe/blob/develop/frappe/commands/gettext.py)
- [Frappe extraction and PO synchronization](https://github.com/frappe/frappe/blob/develop/frappe/gettext/translate.py)
- [Frappe installation requirements](https://docs.frappe.io/framework/user/en/installation)
- [Git merge](https://git-scm.com/docs/git-merge)
- [GitHub fork synchronization](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/syncing-a-fork)
