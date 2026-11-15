# Research exploration map

**This is an independent source copy in IterLab Codex.** The build output is now
`../../static/vendor`, loaded from `/vendor/exploration-map.js` and `.css`.
The host uses `/api/records/<id>` for details. It never imports or calls the
original application. Component regression tests live in `test/`; the checked-in
bundle and license notices let runtime users view the page without Node or a CDN.
This component belongs to a reference prototype, not a fully accepted research product.

This native JavaScript component uses **AntV X6 3.1.8** (SVG/HTML rendering, graph interaction) and **Dagre 3.1.1** (hierarchical layout). It does not depend on React or on a runtime CDN. `tslib` is explicitly pinned because the published X6 ESM modules reference it.

Upstream sources and technical documentation:

- https://github.com/antvis/X6 (MIT)
- https://x6.antv.antgroup.com/en/tutorial/getting-started
- https://github.com/dagrejs/dagre (MIT)

## Build and test

Run `npm ci`, then `npm run build` and `npm test` in this directory. Commit `package-lock.json`, component sources, and the built static assets; do not commit `node_modules`. The normal Python application serves the checked-in bundle without Node.js installed. Dependency license texts are emitted to `../../static/vendor/exploration-map.NOTICE.txt`; esbuild also emits linked legal comments. On Node versions without `--test-isolation=none`, use `node --test test/*.test.mjs` with normal per-file isolation.

This shared repository does not include the original project's browser E2E driver. `npm test` runs the component regressions in `test/`; those tests do not prove browser interaction or end-to-end research. Historical private browser evidence is not a runnable command in this checkout.

## Integration

Load `/vendor/exploration-map.css` and `/vendor/exploration-map.js` from this host (or its versioned `/assets/<revision>/vendor/` paths). Give the parent container a nonzero height.

```js
const map = window.IterLabExplorationMap.mount(container, {
  compact: true,
  data: explorationResponse,
  loadDetail: nodeId => api(`/api/records/${encodeURIComponent(nodeId)}`),
  onSelect: node => updateLocation(node.id),
  onOpenSource: (source, node) => navigateToOriginalEvidence(source, node),
  onExpand: () => openExplorationDialog(),
  renderMarkdown: text => renderSanitizedMarkdown(text),
});
```

`renderMarkdown` must be the host's sanitized Markdown renderer. Without it, the component renders plain text paragraphs. Card text, graph titles and evidence paths use DOM `textContent`. `renderRecord` may supply the host's readable evidence view; the standalone fallback renders labeled fields, lists and nested records rather than a JSON block.

Public instance methods:

- `update(data)`: replace the read-only graph projection; no viewport reset on synchronization.
- `select(nodeId, {notify: true})`: select and load details without centering.
- `clearSelection({notify: true})`: close details and clear upstream emphasis; optional `onDeselect` lets the host clear a deep link.
- `reveal(nodeId)`: unfold ancestors, reveal a filtered node, select and center it.
- `frameContext(nodeId)`: reveal an exact known node without selecting or loading details, then frame the visible direct structural neighbours (reference neighbours only when enabled). Uses measured legend/control safe bounds. If the group would fall below 72%, frames the exact node and reports off-screen associations rather than unreadable miniature cards. A hidden canvas defers until real dimensions; project changes invalidate the deferred target.
- `fit()`, `focusCurrent()`, `focusRoot()`, `resize()`. `focusCurrent()` clears historical emphasis and uses `frameContext` without creating a new research action.
- Mount option `compactDetails:false`: use the host object summary instead of the duplicate compact detail; full-size details remain unchanged. The default stays compatible.
- `setCompact(boolean)`: switch sidebar/full-screen presentation. Each presentation retains its own viewport.
- `setSpineOnly(boolean)`: show key milestones or the complete saved process. Projection never modifies source records.
- `getViewport()` / `setViewport({scale, tx, ty})`.
- `destroy()`: disconnect observers, dispose X6, invalidate pending details and remove the DOM instance.

Detail loading is selection-token protected. Every new graph revision invalidates the detail cache. Project changes reset local selection/collapse/viewports. The host still owns session/request identity checks and synchronization; this component does not poll or execute research actions.

Sources emitted by `onOpenSource` include `{kind:'message', seq, origin_session_id}` and `{kind:'plan', ...node.ref, origin_session_id}`. Result records and exact evidence references are readable in the detail panel without switching sessions.

Primary edges use a top-to-bottom Dagre hierarchy. Secondary evidence references are hidden by default and remain available in the detail panel. The “显示支持、反驳与参考关系” toolbar switch reveals dashed semantic edges. `supports`, `refutes`, `contradicts`, `tests`, `uses`, `compares`, `related_to`, `revisits`, `recorded_after`, `motivates`, `leads_to` and `informed_by` never become hierarchical parents, even when malformed input labels them primary. Plans, branches, trials and results remain distinct. Branch folding and hiding parked nodes only affect presentation, never stored records.

The key-spine view uses explicit `milestone` / `importance` flags and conservative kind defaults. Explicit host execution targets remain visible even when not milestones. Hidden structural subgraphs are represented as `collapsed_path` proxies with `basis:'unknown'`, `projection_only:true`, `original_edge_ids` and `hidden_node_ids`. These IDs identify the complete original hidden subgraph, not an invented direct scientific relationship or an ordered causal path. Clicking a proxy opens the full saved process. Semantic relationships are never contracted into a support/refutation shortcut.

`execution_location` is an independent host observation: `{state, phase, phase_label, summary, reason, next_action, active_node_ids, focus_node_id, retained_node_ids, observed_at, source_session_id, source}`. States are `running`, `waiting`, `paused`, `completed`, `idle` and `unknown`. Only `running` plus explicit visible `active_node_ids` marks executing cards. Retained research directions, old `active` flags, saved statuses and selection never establish live execution. A missing or unbound focus disables location navigation; no last-node fallback is used. Selecting historical evidence leaves this location unchanged. The host should include location changes in its update/revision signaling. `research_change`, `assessment` and `review_history` details have dedicated readable sections for recorded claims, limitations, evaluation evidence and preserved revisions.

Layout geometry is keyed only by visible node IDs, structural edges and compact/expanded card dimensions. Updates to status, prose, live location, selection or semantic overlays retain existing X6 node instances and coordinates; visible edge styles/labels update separately. A topology change recalculates hierarchy while preserving the user's viewport.

Initial framing never selects a node or requests details. Explicit executing nodes may be highlighted without opening a panel. The first compact/expanded view fits the full tree only when the resulting scale is at least 55%; longer trees open at 80% near the research root (or an explicitly selected node). “显示全图” always provides the full overview, even at a small scale; “回到研究起点” and “定位当前执行或等待对象” provide readable navigation. The mouse wheel zooms around the cursor without a modifier key; dragging blank canvas with the left or middle button pans. Wheel events over details, search or controls do not zoom the canvas. Node cards are keyboard selectable.

Selecting a node emphasizes its primary upstream ancestry and connecting edges; unrelated nodes and reference edges fade without becoming unclickable. Search cannot dim the selected ancestry. Closing details, clicking blank canvas, or pressing Escape while the canvas has focus restores normal colors and notifies `onDeselect`. Dragging blank canvas preserves selection. Synchronization and compact/expanded changes retain the selected path. This is presentation only; reference links are never treated as causal parents.

## Default mainline presentation

UI revision 2026-10-29: for the explicit plan-series host, key view now groups ordinary experiment/result/decision records into read-only `process_group` cards under their exact plan. Plans, round reviews, critical findings and the live execution lineage remain visible. Group counts include explicit abnormal records; clicking a group or a search result expands only that plan. Full view retains every original node. `selectionFocus` adds only direct visible reference neighbours to the saved upstream path, without changing ancestry. Card type colours stay unchanged while unrelated cards fade. Native canvas clicks supplement X6 blank events, with drag-click suppression. The toolbar uses scoped responsive form rules and two explicit view choices.

The Codex host now supplies optional `plan_series.schema=iterlab-plan-series/v1` metadata. For this projection, family headers group exact plan versions on one horizontal row, each plan owns its experiment subtree, and major successor families follow below the previous section. Semantic evidence/revision links remain secondary and never change these ranks. Filtering preserves the family metadata. This explicit projection supersedes the legacy record-order serialisation below; legacy graph inputs still retain their original behaviour. A family header is a derived grouping, not a research result or an execution grant.

The default key view consumes optional host `research_path` facts (`iterlab-research-path/v1`). The retained lineage cursor identifies a retained route, never proof of execution. Routes outside it can be folded only when they have no recorded process: authored plans and reviews, source investigations, reported findings/discussion, explicit decisions, revisions, failures, runs and results all preserve their route. Definition snapshots alone are not authored plans. The toggle explicitly reports folded routes as “未展开 N”; the complete view and search retain every original node and detail.

Recorded iterations under the same branch/plan parent are placed on a vertical track. Display-only `record_order` links are dashed, arrowless and labelled “记录顺序”; they mean saved membership/order, not that one experiment caused another. The original parent edges remain unchanged in `data`, full view and detail, and selection ancestry always uses those source edges. Different exact proposal plan versions or mixed version histories are not joined into one track. Actual work, failures, source investigations and meaningful discussion survive even without a training run.

An explicitly persisted `branch_policy.role=mainline` receives the “方案主线” label. A legacy cursor alone never establishes that purpose; a branch creation-time plan reference is not current approval. This component still has no graph mutation, execution or approval endpoint.

## Evidence-backed research narrative

The Codex host can supply `research_story.schema=iterlab-research-story/v1`.
This is an additive historical annotation, saved as append-only
`research.story.saved` events. Original records, exports, messages, deliveries,
approvals and runs stay unchanged. Existing record IDs retain their original
body in the detail panel. Derived context/question nodes have `projection_only`;
their annotation date is separate from the occurrence date of actual records.
A later synthesis does not inherit its source record's occurrence time.

Cards expose historical synthesis, actor, and actual input provenance.
`research_step` means a cited continuation of investigation, not scientific
causality. `claim_revision` remains a secondary reference, but is visible by
default and carries an explicit old/new record relation. `raises_question`
leads to an unverified question, never a scheduled training job. Ordinary
chronological `recorded_after` links retain their old secondary semantics.
Negative record states survive annotations. With no actual plan families, a
long narrative opens at a readable research origin instead of a tiny series
overview. The compact object summary uses the same narrative provenance.

New native records may use the existing `research_record` tool's optional
`research_context` (`role`, `continues_record_id`, `revises_record_ids`). The
host validates same-project references and derives input association from an
exact native turn, then reuses `research_input_origins` to distinguish user,
review and background input. Unknown/multiple input associations stay unknown.
No title parsing, LLM summarization loop, or inferred autonomy is involved.

Historical annotations use `/api/projects/<id>/research-story` (GET/POST), with
optimistic `expected_revision`, exact source excerpts and source-text digests.
The API validates references, chronology-independent relation declarations and
acyclic investigation steps; it does not certify the scientific interpretation
of the human-authored annotation. Synthetic nodes use the existing record-detail
endpoint. The original RoadModel case and its private receipts are not distributed.
For shared scope and limitations, see [the reference status](../../docs/preservation-status.md).
