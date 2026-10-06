# Dual-asset hero sprites

The Dark Carnival UI draws each hero **twice, with completely different artwork**, so the
agent needs two sprite families per hero (see `HERO_DB` in `scripts/dota_logic.py`):

| Folder        | Asset           | Where it appears                | Used for |
|---------------|-----------------|---------------------------------|----------|
| `emoji/`      | `emoji_*.png`   | Ticket / reward screen (pixel art) | Parsing which tickets a game awarded |
| `portraits/`  | `portrait_*.png`| Hero pick grid (high-res 3D)    | Locating and clicking the hero to pick |

## Capturing your own

1. Run the app, open the **Dev Console**, and start the agent in *Simulation mode*.
2. Screenshot the relevant screen at your native resolution.
3. Crop **tightly** around the sprite — no surrounding UI chrome, no drop shadows.
4. Save as PNG using exactly the filename listed in `HERO_DB`
   (e.g. `emoji/emoji_pa.png`, `portraits/portrait_pa.png`).

Template matching is scale-sensitive: capture at the same resolution you play at. If a
hero is not being found, lower `VisionConfig.template_threshold` (default `0.80`) or
re-crop the sprite.

> Real Dota 2 artwork is copyrighted and therefore **not committed** to this repository
> (`.gitignore` excludes `*.png` here). The agent degrades gracefully when a sprite is
> missing: it falls back to reading the hero's Russian name with OCR.
