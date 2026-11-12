# Model discovery: design documentation handoff

Result: ordinary extension of the inherited dark Agent. No design-system changes. This pass writes only this review record; `DESIGN.md` and `.impeccable/design.json` remain byte-for-byte intact. PRODUCT.md and the surface brief already describe the delivered scope and need no change.

## Evidence checked

- `PRODUCT.md`: local, single-user installation; personal credentials per purpose; explicit discovery, testing and saving; OCR consent and deliberate vector activation/reindexing.
- `DESIGN.md`: incumbent frontmatter, palette, typography, responsive dialog geometry, buttons/fields, focus behavior and named rules.
- `.impeccable/design.json`: schema v2, extension metadata, motion/breakpoints, component inventory, native-settings preview and narrative rules. No token or preview regeneration.
- `design/model-settings.md`: THESIS, OWN-WORLD, STORY, FIRST VIEWPORT and FORM direction, including independent drafts, manual fallback and 320px reflow.
- `app/web/workspace.css`: root tokens and inherited control rules; all model-settings, purpose, choice, inline-option and status rules; effective mobile overrides, including the more-specific 16px field/select rule.
- `app/web/index.html`: settings dialog, three purpose controls, native fields/selects, manual entry, dimensions, activation, fee notice, live status and scoped actions (lines 114–148).
- `app/web/workspace.js`: settings state, presets, metadata compatibility, discovery, selection, purpose switching, saving/testing and clearing (lines 28–145), plus field events and close cleanup (lines 750–771).
- `docs/user-guide/model-settings.md`: user sequence, independent credentials, three synthetic test types, consent, dimensions, reindexing and compatibility boundaries.
- `reports/model-discovery/README.md`: delivery scope, supplied test results, synthetic browser evidence and retained detector limitations.
- `.impeccable/review/model-discovery/finish-review.md`: independent final disposition `ship`, all 12 captures valid, no material fixes.
- `.impeccable/review/model-discovery/browser-checks.json`: 12 viewport/state records without document overflow, real native-control interactions, no browser errors, zero provider requests and zero persistent user writes.

The documenter instructions and `reference/document.md` were read. Their ordinary-extension path applies: preserve the confirmed incumbent system and report drift without adopting it into future rules. No standalone design questions, app edits, browser session, render, capture, detector rerun, network request or installation was required for this handoff. No real environment or private model-settings file was read.

## Five-line system summary

1. Palette: inherited charcoal paper (`#191a19`) and surface (`#222321`), bright neutral ink (`#eceee8`), muted copy (`#a5aaa0`) and restrained leaf-green actions (`#c3d2bd` / `#d0dfc9`); textual status preserves **The 文字可核对 Rule**.
2. Type: inherited Segoe UI / Microsoft YaHei / PingFang SC / system sans; settings title 21px desktop and 19px at ≤600px, labels 13px, inputs/selects 14px desktop and 16px mobile, hints 11px; **The 等宽有用途 Rule** remains unchanged.
3. Layout: protected native dialog, width `min(560px, calc(100vw - 32px))`, 16px corners and bounded internal scrolling; ≤600px uses `calc(100vw - 24px)` with a viewport-bounded height; three compact purpose buttons reveal one form, retaining **The 平面分层 Rule**.
4. Form: native provider/model selects, URL and masked Key inputs, named manual-entry fallback, 8px control corners, inherited fine borders, 42px general control minimum and 3px visible focus; provider/address and Key precede explicit fetch, selection, optional test and purpose-scoped save.
5. State: live textual loading/error/success feedback, busy-disabled controls, purpose-specific saved-Key indicators and draft fields, manual recovery, fee/transmission copy before tests, two-click scoped clear, and close-time Key cleanup/focus return; no new visual vocabulary or global rule is introduced.

## Delivered surface and truth boundaries

The source supports the direction contract: users can select a returned identifier without memorizing it; presets fill API addresses only; discovery, optional testing and saving are separate actions. The list is explicitly described as insufficient to guarantee current-purpose suitability. A failed or unavailable catalog preserves the fields and names manual recovery.

Purpose switches toggle the existing fieldsets without refilling them, so each purpose's unsaved field draft remains separate within the open dialog. Saving refills only the selected purpose. Closing clears all three Key inputs, restores password masking, invalidates pending settings responses and returns focus to a connected opener or the sidebar toggle. Address changes clear that purpose's entered Key and require a replacement when it differs from the saved address. No Key is copied from another purpose by this frontend flow.

OCR copy retains per-use external-send consent. Embeddings expose dimensions and a separate enable checkbox with document-transmission and fee disclosure. Saving uses the selected purpose's settings endpoint; testing uses the check endpoint. The embedding save result explicitly says existing documents do not rebuild automatically. These are source-grounded UI statements, not an independent backend enforcement or model-accuracy audit.

PRODUCT.md, the guide and delivery record describe preserved chat v1 compatibility, personal-only OCR/vector runtime Keys without developer-environment fallback, configuration snapshots for running work, and deliberate old-index rebuilding. The frontend also accepts legacy single-chat metadata and purpose profiles. Runtime guarantees are attributed to the supplied delivery/test record; no backend audit is claimed here.

The independent finish review reports `ship`, all 12 captures valid and no material fixes. Its desktop, 390px and 320px coverage includes the deliberately scrolled lower embedding actions. The browser record uses intercepted synthetic API fixtures and catalog identifiers; its interaction results establish interface behavior, not provider availability or recognition accuracy. The delivery record reports 620 passing Python tests with real PostgreSQL/Redis enabled and 55 passing frontend tests. This documentation pass did not rerun those checks; the separate final verification record owns the final check evidence.

## Preserved files

The following SHA-256 values were checked before and after writing this record:

| File | SHA-256 |
| --- | --- |
| `DESIGN.md` | `54CC7D5A3CB9547DF36E3972AAA8B6F0C82B753B6ABD6C18831D6EEC101B9D5D` |
| `.impeccable/design.json` | `7E1088290D575E2995E857860072C90F6DE0EFF2F36D9713B93A0B1DD314D436` |

No new world, composition, seed, palette, font, raster asset, dependency or token rewrite is warranted. The extension's behavior belongs in its existing surface brief and guide; global primitives remain incumbent.

## Drift not canonized or repaired

The retained sidecar settings snippet is an earlier static chat-only preview, and DESIGN.md's Personal API Settings examples retain earlier button wording. They were not refreshed under the explicit byte-preservation boundary and are recorded as documentation snapshot drift, not new rules or proof of the current three-purpose composition. As documented by the delivery and finish-review records, the supplied detector result also retains the incumbent hidden image preview without an initial `src`, unresolved `/assets/` stylesheet/default-heading inference and older token-parser advisories. No new ignore or token change was added to suppress them. The independent review found no material defect in this settings surface; this ordinary extension does not authorize repairing unrelated incumbent drift.
