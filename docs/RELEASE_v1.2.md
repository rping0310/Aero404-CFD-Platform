# v1.2 — Liquid Glass

## Source and scope

Application source was exported from the running deployment for this release.
Local prototype/runtime code was not used as the application source. Existing
public example configuration, account-creation tooling and isolated test fixtures
are retained from the GitHub repository. No live service was restarted or changed
to prepare this release.

## Interface

- Smoke, Light and Dark appearance; glass and motion controls.
- Shared administrator-managed wallpaper gallery. Requires Pillow.
- Navigation pinned by default, with a saved personal pin preference in Gallery.
- Unpinned navigation overlays the page; reveal/hide does not resize content.
- Flush-left desktop navigation without the old oval toggle.
- Improved Main/Sub selection contrast and semantic colors in Light mode.
- Independent CSV selection for each plot, hidden scrollbars with scrolling retained,
  and improved file-picker and dialog presentation.

## Rendering performance

- Capture only changed scene sources and pause the optical renderer when idle.
- A pinned desktop dock captures the wallpaper rather than unrelated workspace updates.
- Keep actual underlying workspace refraction when the dock is unpinned.
- Hide the optical shell immediately and reject stale captures after hide/show.
- Avoid per-frame DOM capture during dock transitions; retain native glass fallback.
- Runtime counters are available at `window.aero404GlassStats`.

Performance depends on browser/GPU and scene complexity; this is not a universal
frame-rate guarantee. Existing case identity, role checks, queue behavior, plot,
slice and STL features remain in place.

## Privacy and packaging

- No production `data/`, accounts, password hashes, sessions, uploaded wallpapers,
  login logs, jobs, SSH keys, certificates, simulation cases or result files.
- Deployment-specific hosts, usernames and home paths are replaced by examples.
- Test fixtures are synthetic and create their own temporary configuration.
- Existing copyright/contributor information and third-party notices are retained.
- Bundled default images are checked for EXIF/XMP metadata before publication.

## Upgrade

1. Back up application files and your private `data/` directory separately.
2. Install the release dependencies, including Pillow, in your application environment.
3. Preserve your real `data/`; never replace it with `data.example/`.
4. Use a single Uvicorn worker: sessions, queue ownership and gallery locking are
   process-local. Do not add workers without redesigning shared state.
5. Backend changes require a planned web-service restart. Check that the installed
   systemd service uses `KillMode=process` to protect local solver processes.
6. Hard-refresh browser assets after upgrading. Use HTTPS for remote login.

There is no new volume-field export, surface-difference or iteration-tree feature
in this release; those remain separate proposals.
