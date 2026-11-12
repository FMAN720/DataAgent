# Core analysis design documentation handoff

2026-11-05. This is an ordinary extension of the confirmed dark conversational Agent. The shipped source preserves the existing visual world; it does not establish a new design system.

## Files checked

- `PRODUCT.md` — product purpose, core capabilities, local configuration and validation limits.
- `DESIGN.md` — incumbent frontmatter, canonical sections, component behavior and named rules.
- `.impeccable/design.json` — schema v2 metadata, typography, breakpoints, depth/motion, representative button/field/navigation/composer/answer snippets and narrative.
- `design/core-analysis.md` — approved extension direction and advanced JSON configuration boundary.
- `app/web/workspace.css` — root tokens, effective conversational type/layout, native dialog and inspector surfaces, business/task/validation rules, responsive overrides and intrinsic-content fix.
- `app/web/index.html` — capability-gated navigation, central composer, profile selector, optional inspector and native business-definition dialog.
- `app/web/business.js` — profile revisions, editor generation guards, native dialog focus return, task stages/attempts and explicit recovery.
- `app/web/workspace.js` — answer/detail separation, numeric validation states, recovery draft preservation and dialog keyboard protection.
- `reports/core-analysis-upgrade/README.md` — implementation boundaries, reported 600 Python / 51 frontend / 21 offline checks, and evidence limitations.
- `.impeccable/review/core-analysis/finish-review.md` — independent final disposition.
- `.impeccable/review/core-analysis/finish-browser-checks.json` — bounded post-fix geometry and synthetic interaction record.

## Five-line system summary

1. **Palette:** inherited charcoal navigation `#141514`, canvas `#191a19`, surface `#222321`, bright neutral text `#eceee8` and leaf-green actions `#c3d2bd`; semantic danger/warning colors supplement explicit text (`workspace.css` root; `DESIGN.md` Colors).
2. **Type:** native Segoe UI / Microsoft YaHei / PingFang SC sans, 15px interface root, 16px answers at 1.9 and composer at 1.75, with 32/29/26px welcome titles; **The 等宽有用途 Rule** keeps monospace for code/evidence and the advanced JSON editor, whose scoped 13px size becomes 16px on mobile (`workspace.css` Typography and business rules; `DESIGN.md` Typography).
3. **Thesis and first viewport:** one question at a time, a central composer capped at 760px (800px on wide screens), restrained navigation and persistent follow-up input; capability-gated task/business actions extend those surfaces without a dashboard (`index.html`; `workspace.css` conversation layout; `business.js` initialization/sync).
4. **Story and truth:** the answer remains the main body with one optional details action; scope, evidence, profile version, task attempts and validation stay inspectable on demand, including explicit partial/failed/not-applicable wording and “未进行数值认证”; **The 文字可核对 Rule** prevents color or completion from implying certification (`workspace.js` answer/report rendering; `business.js` task rendering; `DESIGN.md` Colors).
5. **Form and depth:** existing 8px controls, 14px panels, 16px native dialog, thin borders and 3px visible focus continue; the JSON editor uses native protected modal focus, tasks use the existing inspector and mobile overlay, and **The 平面分层 Rule** reserves shadows for floating surfaces (`workspace.css`; `index.html` business dialog; both JavaScript files' focus/keyboard handling; `DESIGN.md` Elevation & Depth).

## Extension and evidence boundaries

The JSON editor is advanced mapping configuration, not a general business no-code wizard. Its illustrative template requires real table/column mappings, units and unique keys. Saving requests a new revision with `expected_revision`; generation checks prevent late initial reads, selected-definition reads, imports and saves from replacing newer drafts. Existing conversation bindings do not silently switch to a new version. These behaviors are evidenced in `business.js` and accurately scoped in `PRODUCT.md` and `design/core-analysis.md`.

Task details show stages, attempts and actual records. Recovery uses an explicit two-step action, displays the supplied recovery notice and preserves the composer draft; the delivery report records re-querying current data, possible model fees and server-side version/condition checks. This is manual recovery, not automatic rerunning or task scheduling. Deterministic number/unit/range checks and cross-domain offline tests remain bounded capabilities, not proof of all business semantics or real-model accuracy.

The independent disposition is **ship**: both the 320px welcome clipping and hidden not-applicable state are resolved. The final browser record covers those two fixes and desktop home regression only, with zero provider requests and persistent writes; it does not establish backend accuracy. This handoff inspected source and existing records only; it did not rerun tests, render/capture images or expand the review.

## Preserved system and uncanonized drift

**No design-token-file changes.** `DESIGN.md` and `.impeccable/design.json` remain byte-for-byte unchanged; `PRODUCT.md` and `design/core-analysis.md` also remain unchanged. The recorded capabilities and extension boundaries are already accurate, so this handoff adds evidence rather than regenerating their content.

| Preserved file | SHA-256 before and after this pass |
| --- | --- |
| `DESIGN.md` | `54CC7D5A3CB9547DF36E3972AAA8B6F0C82B753B6ABD6C18831D6EEC101B9D5D` |
| `.impeccable/design.json` | `7E1088290D575E2995E857860072C90F6DE0EFF2F36D9713B93A0B1DD314D436` |

**Drift not canonized or repaired:** the incumbent `DESIGN.md` navigation inventory still says four real entries, while `index.html` / `business.js` now expose two additional capability-gated task/business actions. This is local inventory lag, not a new visual rule or identity change, and remains untouched under the preservation boundary. Its prohibition on unimplemented task scheduling does not prohibit the shipped task inspection/manual recovery. No broader visual-system divergence was identified in the sampled source. Existing detector advisories reported by the delivery README were neither promoted into design guidance nor repaired; they do not justify token normalization or a wider redesign.
