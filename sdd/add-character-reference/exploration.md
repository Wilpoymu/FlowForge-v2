# Exploration: add-character-reference

## Current State

FlowForge v2 **already has 100% of the backend primitives** for reference/character image support. The gap is purely in the bridge API and dashboard UI wiring.

### What exists (backend — `engine/flow_client/_generation.py`)
- `FlowClientInstance.upload_reference(image_bytes_b64, project_id, filename)` — L117-135. Uploads base64 image to Flow project, returns media name, appends to `self.reference_ids`.
- `FlowClientInstance.generate_image(prompt, ..., reference_ids, ...)` — L143-192. Accepts `reference_ids`, falls back to `self.reference_ids`, propagates to Flow API as `imageInputs`.
- `batch_generate(..., reference_image_bytes=None)` — L195+. Already accepts `reference_image_bytes`: on L254-256 it iterates over ref bytes and calls `cli.upload_reference(ref_b64)` per client. On L340 it passes `refs = reference_ids or (cli.reference_ids if cli else [])` to `generate_image`.
- `FlowClientInstance.__init__` has `self.reference_ids = []` — L27.

### What exists (reference — batchy-decrypted)
- `upload_reference()` and `generate_image()` — structurally identical to FlowForge v2.
- `batch_generate()` — identical signature, same `reference_image_bytes` handling.
- `_project_personaje_files()` — scans `<project>/personaje/` for `.png/.jpg/.jpeg/.webp`, returns files. Auto-detects character images.
- Dashboard UI — `st.file_uploader()` with `accept_multiple_files=True`, preview thumbnails in columns, auto-detect fallback from project folder.
- Batch flow — reads file bytes, base64 encodes, uploads to each client, passes `reference_image_bytes=ref_images_b64` to `batch_generate`.

## Affected Areas

| File | Why affected |
|------|-------------|
| `engine/flow_client/_bridge.py` L361-438 | POST `/api/generate` handler does not accept `reference_images`. Needs to: (a) read base64-encoded images from request body, (b) decode if needed, (c) pass `reference_image_bytes` to `batch_generate()`. |
| `dashboard/index.html` L762-804 | No reference image uploader exists. Needs: file input (accept multiple), preview thumbnails, clear button, optional auto-detect indicator. |
| `dashboard/app.js` L121-196 | `startBatch()` does not read or send reference image data. Needs: read selected files, base64 encode client-side, include in POST body, auto-detect from project API. |
| `engine/flow_client/_projects.py` L38-57 | `_scan_project_folder()` does not scan for `personaje/` subdirectory. Needs: detect `personaje/` folder, list image files, expose in project dict. |
| `engine/flow_client/_bridge.py` do_GET | Needs new endpoint: `GET /api/projects/{name}/references` to list auto-detected reference images in project folder. |

## Approaches

### Approach 1: Minimal — Wire existing backend to UI

Add file input to dashboard, encode base64 in JS, send in POST body, bridge passes through to `batch_generate`.

- **Pros**: Smallest change (~80 lines). Uses 100% existing backend. Fast to implement. No project scanning.
- **Cons**: No preview. No auto-detect from project folder. User must re-upload every session. No multi-image support without extra work.
- **Effort**: Low

### Approach 2: Full — Auto-detect + uploader + preview + project scanning

File uploader with drag-drop, preview thumbnails, auto-scan project `personaje/` folder, API endpoint for detected images, multi-image support.

- **Pros**: Matches batchy-decrypted UX. Auto-detect saves time. Preview confirms correct images. Multi-image for complex scenes. Project-aware.
- **Cons**: More code (~150-200 lines). More moving parts. Need new API endpoint. Base64 encoding for large images could bloat POST body.
- **Effort**: Medium

### Approach 3: Server-side upload via temp file

Instead of base64 in POST body, upload files to a temp endpoint, return ref IDs, then pass ref IDs in generate request.

- **Pros**: Avoids base64 POST bloat. Cleaner separation. Could cache uploads across batches.
- **Cons**: Significant complexity. Two-step UI flow. State management for temp files. Overengineered for current needs.
- **Effort**: High

## Recommendation

**Approach 2 — Full (auto-detect + uploader + preview + project scanning).**

Here's why:

1. The backend is already ready — `batch_generate(reference_image_bytes=...)` works today. The gap is purely wiring.
2. batchy-decrypted already validated this exact UX pattern. We're porting a proven design.
3. The approach breaks down into 4 independent, small tasks that can be implemented incrementally:
   - **Task A**: Add `personaje/` scanning to `_scan_project_folder()` + expose via API (~20 lines)
   - **Task B**: Add `reference_images` to bridge POST `/api/generate` + pass to `batch_generate()` (~15 lines)
   - **Task C**: Add file uploader + preview to dashboard HTML/CSS (~60 lines)
   - **Task D**: Wire JS: read files, encode, send in POST, auto-detect from project API (~60 lines)
4. No new dependencies — everything is vanilla JS, base64 encoding via `FileReader.readAsDataURL`, standard browser file input.
5. The project auto-detect feature is what makes this *feel* integrated rather than bolted-on.

## Risks

- **Large base64 POST bodies**: Reference images can be several MB. Base64 encoding adds ~33% overhead. Mitigation: limit file size client-side (e.g., 5MB), which matches batchy behavior. Could add chunked upload later if needed.
- **Upload failure blocks entire batch**: If `upload_reference()` fails for any client, the batch fails for ALL clients. Mitigation: this is existing behavior in batchy-decrypted, not new. Could add retry logic in a future iteration.
- **Duplicate uploads per client**: With multi-account mode, the same reference is uploaded N times (once per client). Mitigation: Flow might deduplicate by content hash; unknown. Worth testing.
- **No cleanup of uploaded references**: Flow projects accumulate uploaded references. Mitigation: projects are ephemeral (UUID-based), garbage-collected by Flow eventually. Low risk.
- **File input UX on mobile**: Multi-file input + preview on small screens. Mitigation: current dashboard already has responsive CSS; add media queries for reference section.

## Ready for Proposal

Yes. The exploration confirms the backend is ready, the gap is well-understood, and the implementation breaks down cleanly into 4 independent tasks. Ready to proceed to proposal (`sdd-propose`).
