# CLAUDE.md - FTE Studio (read first, any Claude account)

**Continue work from `PENDING.md`** (prioritised task list + exact commands).

Goal: Figma (.fig / plugin zip) -> free exact extraction (JSON, assets, HTML/CSS) -> native Elementor (Elementor PRO + ACF PRO in production) -> WordPress, measured accuracy. Desktop app + WP plugin + MCP. Claude is used only for final polish, with few tokens.

## MUST follow: DeveloperAI skill
https://github.com/vijays-wliq/DeveloperAI -> `CLAUDE-ELEMENTOR-WORDPRESS-CONVERSION-EXPERT.md` (Elementor) and `PROJECT-BOOTSTRAP.md` (Figma -> HTML).
Key rules the code implements (see docs/SKILL-COMPLIANCE.md): s96 match the design; s98-102 Global Colors / Typography / Layout first;
s113 native widgets (no page-in-HTML-widget); s114 NO custom CSS / class-based CSS (fonts @font-face via the bridge = approved theme CSS);
s118 container width set once in the Kit; s131 WebP; s140 upload media once; s141 `<meta name="DeveloperAI" content="Developer Skill">` once per page;
s105-108 header/footer via Theme Builder (Pro); s145 every link set (links_todo list). Report any conflict instead of silently breaking a rule.

## Rules
- Token-cheap: call `fte_make` (MCP) / `studio.py make` first: one call, 6-8 line result. Only read JSON/briefs when needed (`fte_frame_brief` is capped).
- Accuracy is MEASURED, never claimed: `compare.py` (tolerant + strict) and `tests/t_sections.py` (section y/height drift). Say what was not measured.
- Never write a WordPress Application Password to a file. Env vars FTE_WP_URL / FTE_WP_USER / FTE_WP_PASS only. Test-site credentials live in F:\wp-fte-test only.
- Test site (isolated, never the user's sites): `python tools/make_test_site.py` then `python tools/test_server.py` -> http://127.0.0.1:8088 (admin / fte-test-pass). Viewer: /fte-compare.html.
- Real test file: C:\Users\HP\Downloads\Buell Client.fig (+ fonts.zip). Exports: F:\figma-export-kit\_real\out.
- Python: F:\figma-export-kit\.venv (or studio\.venv after Setup.bat). Run Python on downloaded/extracted data with `-I`.

## Layout
studio.py (desktop app + CLI) | core/: fig_import+kiwi (offline .fig), figma2html (exact HTML), elementor_native (native Elementor builder: exact + responsive modes),
pipeline (build/push), dynamic (CPT/ACF/Loop Grid), raster (masks), media (WebP), wp_client, compare/verify, ai_openrouter (optional, free models), mcp_server
| wp-plugin/fte-studio-bridge | figma-plugin/ | tests/ | tools/ | docs/GUIDE.html (visual guide)

## Status (2026-11-26) - update after each session
- Elementor pixel-exact vs exact HTML (Buell Home 1920x9721): 96.86% tolerant / 95.09% strict, 12/12 sections exact y/height.
- Responsive (inferred flow) mode: 48% - needs work (text wrap, cards).
- Dynamic (CPT + ACF PRO fields + posts) + skill checks: tests/t_dynamic.py 13/13 on WP 7.0 + Elementor 4.4.2 + ACF 6.8.9.
- NOT tested: Elementor PRO (Theme Builder templates, Loop Grid, ACF dynamic tags, Pro forms/nav) - no Pro licence on the test site.
- NOT measured: HTML/Elementor vs Figma's OWN render (no Figma Desktop here). Next: user exports frame PNG -> verify.
- Not done: Figma prototype animations -> Elementor motion effects; responsive from tablet/mobile frames merge; nav-menu & form widgets (Pro).
