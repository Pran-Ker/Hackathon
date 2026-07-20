# CLAUDE.md

Guidance for Claude Code when working in `ios/Hologram`.

## What this is

**Hologram** — the POPFRAME iOS client. Capture/pick a photo or video + a text prompt, POST it to the POPFRAME backend (`POST /api/scene`, multipart `file` + `prompt`), then show the resulting hologram in a WKWebView at `<base>/app/hologram.html?scene=<id>`. Cloned from the TestShip template (`~/Developer/mobile-dev/TestShip`); fastlane/TestFlight scaffolding was intentionally omitted for now (local builds only) and can be re-added from the template when shipping.

## Rules

- The `.xcodeproj` is generated from `project.yml` (XcodeGen) — **never edit the `.xcodeproj` directly**. After changing `project.yml`, run `xcodegen generate`.
- This directory lives inside the Hackathon repo (no nested git repo).

## Commands

```bash
# Regenerate the Xcode project
xcodegen generate

# Build for simulator (Debug config has code signing disabled)
xcodebuild build -project Hologram.xcodeproj -scheme Hologram \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -quiet

# Run in the simulator: open Hologram.xcodeproj in Xcode, pick a simulator, Cmd-R
```

## Architecture (Sources/Hologram/)

- `HologramApp.swift` — @main entry, shows `RootView`.
- `RootView.swift` — phase state machine: capture → scanning → hologram; hosts the settings sheet.
- `CaptureView.swift` — PhotosPicker (images+videos) + in-app camera (`CameraPicker.swift`, UIImagePickerController) + prompt field + SCAN button + demo-scene shortcut.
- `ScanningView.swift` — upload progress, long-wait copy (~60s uncached, instant cached), error state with Retry / demo fallback.
- `HologramView.swift` — WKWebView loading `<base>/app/hologram.html?scene=<id>` (inline media + JS allowed); native fallback view showing Scene3D data when the viewer is unreachable; share link + snapshot-to-Photos.
- `SettingsSheet.swift` — user-configurable base URL (`@AppStorage("baseURL")`, default `http://localhost:8000`) + Test connection (GET `/api/health`).
- `UploadClient.swift` — multipart POST `/api/scene`, 900s timeouts, health check, bundled demo scene loader.
- `Models.swift` — lenient Scene3D decoding (contract: scene_glb_url, frame_url, prompt, objects[], cached, timings) + `resolvedSceneID` (scene_id / id / derived from asset URLs).
- `SampleScene.json` — bundled mock Scene3D so the app demos with the backend down.
- `Info.plist` — manually maintained; camera/mic/photo-add/local-network usage strings and an ATS exception (`NSAllowsArbitraryLoads`) for plain-HTTP dev servers. Tighten ATS before any App Store submission.

## Backend contract (open items)

- `POST /api/scene` should include a stable `scene_id` in the response — the documented contract lacks one, so the app falls back to deriving the id from `scene_glb_url` path segments.
- The viewer must be served at `/app/hologram.html?scene=<id>` on the same origin as the API.
