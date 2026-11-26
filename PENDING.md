# PENDING - continue from here (new Claude account/session)

Read first: `CLAUDE.md` (rules + DeveloperAI skill), then this file. Work folder: `F:\figma-to-elementor-latest\studio`.
User: Gujarati/Hinglish, wants FREE + token-cheap + 100% accurate Figma -> HTML/CSS/JSON/assets -> WordPress (Elementor PRO + ACF PRO in production).
Python for dev: `F:\figma-export-kit\.venv\Scripts\python.exe` (3.14, Pillow, Playwright, PyInstaller).

## State on 2026-11-26 (what works, measured)
- Desktop app `studio.py` (Start / 1. Design / 2. WordPress / 3. Build tabs). `Studio.bat`, `Setup.bat`. EXE: `dist\exe\FTE-Studio\FTE-Studio.exe` (PyInstaller, 40 MB, launches OK).
- Extract-only (no WordPress): `studio.py extract FILE.fig -o OUT [--pages ...]` -> html/, pages/*.json, tokens, assets/images, assets/svg, figma-extract.zip. Real .fig: 9 frames, 88 images, 212 SVG in ~45 s.
- Elementor native, pixel-exact mode vs exact HTML (Buell Home 1920x9721): 96.86% tolerant / 95.09% strict, 12/12 sections exact.
- Whole site: `studio.py site OUT [--pro]` -> tested on test WP: Home + About pages + Main Menu.
- MCP (`core/mcp_server.py`, or `studio.py mcp`): fte_extract, fte_make_site, fte_make, fte_compare, fte_frame_brief, fte_list_frames, fte_wp_info. Outputs are 4-8 lines (token-cheap).
- WP plugin `wp-plugin/fte-studio-bridge` (pages, kit, templates, CPT+ACF, posts, menu, fonts, SVG upload, DeveloperAI meta). tests/run_tests.py 12/12, t_dynamic.py 13/13.
- Isolated test WP: F:\wp-fte-test (WP 7.0, Elementor 4.4.2, ACF PRO 6.8.9). Start: `python tools\test_server.py` -> http://127.0.0.1:8088 (admin / fte-test-pass). Viewer: /fte-compare.html.

## PENDING (in priority order)
1. **EXE**: freeze fix DONE and verified (EXE extract of the real .fig -> 9 html, 9 json, 88 images, 212 svg).
   Still to do: click through the EXE GUI once (Start -> Extract; WordPress connect; Build), and zip `dist\exe\FTE-Studio` for sharing.
2. **Accuracy vs REAL Figma (user wants 100%).** Nothing has been compared with Figma's own render yet (no Figma Desktop on this PC).
   Ask the user: in Figma select frame "01 Home Page Design" -> Export -> PNG 1x -> save e.g. F:\figma-ref\home.png.
   Then add `--figma-png` support: compare (a) exact HTML vs PNG and (b) Elementor page vs PNG using core/compare.py (reference can be a PNG file:
   add a branch in compare.compare() that loads a .png reference instead of screenshotting). Report both numbers honestly; fix what the diff shows.
3. **Remaining Elementor diff (3%)**: worst bands y=5670 (Featured Projects photos) and y=3240 (ghost "Buell Recreation" text + wave). Use
   `tests/t_compare_home.py`, `tests/view.py y0 y1`, `tests/dbg_dom.py <section>`.
4. **Elementor PRO path untested** (no Pro licence here): Theme Builder header/footer (`/ftes/v1/template` with conditions), Loop Grid + ACF dynamic
   tags (core/dynamic.py), loop-item template. Needs a staging site with Pro: run `studio.py site OUT --pro` and fix JSON keys if Elementor rejects them.
   Not built yet: Pro Nav Menu widget (replace header nav), Pro Form widget (from "Input" frames), internal-URL dynamic tags for links (skill 132).
5. **Animations** (user asked "with animation"): read Figma `prototypeInteractions` (ON_HOVER / SMART_ANIMATE / DISSOLVE in the .fig, see fig_import)
   -> Elementor native hover/entrance (Motion Effects). Not started.
6. **Responsive**: pixel-exact mode is a fixed 1920 desktop layout. Responsive (inferred flow) mode is only 48%. Better: merge real Tablet/Mobile frames
   (same page name) into `_tablet` / `_mobile` settings.
7. Extract zip is big (415 MB: original full-size images). Option to also write web-size WebP copies.
8. Update `docs/GUIDE.html` measured table + `CLAUDE.md` status after each step. Rebuild `python tools\make_package.py`.

## Build EXE
```
cd F:\figma-to-elementor-latest\studio
F:\figma-export-kit\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name FTE-Studio --paths core ^
  --hidden-import fig_import --hidden-import kiwi --hidden-import figma2html --hidden-import pipeline --hidden-import elementor_native ^
  --hidden-import dynamic --hidden-import media --hidden-import raster --hidden-import compare --hidden-import verify --hidden-import wp_client ^
  --hidden-import mcp_server --hidden-import ai_openrouter --exclude-module playwright ^
  --add-data "F:\figma-to-elementor-latest\studio\wp-plugin;wp-plugin" --add-data "F:\figma-to-elementor-latest\studio\docs;docs" ^
  --add-data "F:\figma-to-elementor-latest\studio\figma-plugin;figma-plugin" --distpath dist/exe --workpath _work/pyi --specpath _work/pyi studio.py
```
Note: the EXE excludes Playwright, so "Check accuracy" needs the Python version (Setup.bat) for now.

## Old project (for reference only)
`F:\figma-to-elementor-latest\figma-to-elementor-latest` = the user's earlier 2.3 GB project (WP plugin + Node MCP + engine; its 9 test suites pass).
`wliq-wp-academy` inside it is a separate project. Studio replaces the Figma->Elementor part; nothing there was deleted.
