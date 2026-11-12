# Personal API and analysis details: documentation refresh

The documentation-only persistence correction from [finish-review.md](finish-review.md) is complete. This extends the existing dark system; its 54 colors, 25 typography roles, schemaVersion 2, and unrelated component previews are preserved.

## Updated records

- `DESIGN.md` adds `analysis-details-button`, `analysis-inspector`, `analysis-inspector-overlay`, `analysis-inspector-mobile`, `analysis-report`, `model-settings-dialog`, and `model-settings-dialog-mobile` component tokens. Values come from `app/web/workspace.css`; existing primitives are reused.
- Layout keeps generic data/source details at a 380px desktop column and a maximum 420px overlay at ≤1180px. The separate analysis variant uses 440px, changes to `min(480px, 100vw)` at ≤1380px, and keeps the application in one column at ≤900px. Settings use a maximum 560px native dialog, 16px corners, the existing backdrop, and `calc(100vw - 24px)` at ≤600px.
- Component guidance now describes direct answers plus one details action; actual activity, audit, E records, queries, tables/charts, source actions, exports, timestamps, and SQL controls remain in the optional right inspector. It also records the API navigation entry, missing-profile draft preservation, source return, history, and closing focus.
- `.impeccable/design.json` adds independent answer, analysis-panel, and settings previews, updates the existing navigation preview, adds `analysis-overlay-max: 1380px`, and updates the shared inspector-shadow purpose and matching narrative characteristic. The other nine existing previews are unchanged. The settings preview is static and non-operational; its positioning and maximum width merely fit the preview canvas.
- Credential guidance retains the local OS-user installation boundary: Windows DPAPI or private POSIX permissions, metadata-only GET, no browser Key persistence or web developer-Key fallback, and a frozen configuration per running turn. Save is local; testing is explicit and potentially billed. Explicit CLI configuration and separate vision/embedding settings remain distinct. Docker uses loopback publishing, a profile named volume, and an explicitly limited bridge allowance; this is not account or tenant isolation.

## Validation and limits

JSON parsing and round-trip validation passed. Assertions confirmed schemaVersion 2, 54 unchanged color metadata entries, 25 unchanged typography metadata entries, nine unchanged unrelated previews, valid component references, and a clean answer preview without inline technical metadata. The four web source files had identical SHA-256 hashes before and after the sidecar transform; no application source was edited.

This pass only wrote `DESIGN.md`, `.impeccable/design.json`, and this report. It did not read real `.env` files or Keys, run a browser, call a provider, install dependencies, rerun tests/detectors, alter historical evidence, or ship raster assets. The implementation continues to use authored SVG; screenshots remain offline QA evidence.

Existing verification supplied for this extension remains the evidence boundary: all 12 screenshots were opened by the reviewer; `browser-checks.json` passed using mocked settings, connection checks, chat, history, and SQL writes. The prior results are 436 Python tests passed, 94 opt-in integration tests skipped, 45 JavaScript tests passed, Ruff passed, and mypy passed for 82 files; this documentation pass did not rerun them. Real provider interoperability, rebuilt Docker images, physical mobile keyboards, and other browser engines remain unverified. `detector.json` retains its unresolved `/assets/` heading-style and initially source-less hidden vision-preview limitations; no ignore or clean-detector claim was added.
