# Dark Carnival — Cognitive RPA Agent

> 🇷🇺 **Пользователям:** пошаговая инструкция на русском — [ИНСТРУКЦИЯ.md](ИНСТРУКЦИЯ.md).
> Интерфейс программы полностью русифицирован.

A production-grade, modular **Cognitive RPA** desktop agent that automates the cyclical
Dota 2 *Dark Carnival* co-op-bots loop on a **fully Russian game client**.

It does not click coordinates. It **reads the screen**.

```
   ┌─────────────────┐   OCR (ru+en)   ┌──────────────────┐
   │  Dota 2 (RU)    │ ──────────────► │  vision.py       │  "Принять" @ (952, 571)
   │  1920×1080      │ ◄────────────── │  input_handler.py│  humanised Bezier click
   └─────────────────┘   SendInput     └──────────────────┘
            ▲                                   ▲
            │                      ┌────────────┴────────────┐
            └──────────────────────│ executor.py (state machine)
                                   │ dota_logic.py (HERO_DB)  │
                                   │ learning.py (adaptive)   │
                                   └──────────────────────────┘
                                                ▲  importlib hot-swap
                                   ┌────────────┴────────────┐
                                   │ main.py — PySide6 shell │  ← the only compiled code
                                   └─────────────────────────┘
```

---

## Core design principles

| Principle | How it is enforced |
|---|---|
| **No blind coordinates** | Every click targets the bounding box of text the OCR actually read this tick. A test asserts the agent never clicks empty space. |
| **Russian-first** | `EasyOCR(lang_list=['ru','en'])` + a Cyrillic-aware matcher that survives homoglyphs, `ё/е`, and OCR noise. |
| **Hot-swappable logic** | The `.exe` contains only the GUI. All logic is raw `.py` beside it, reloaded via `importlib` without restarting. |
| **Never trust a click** | Each action is verified by the *next* observation; failures escalate through a recovery ladder. |
| **Testable without Dota** | 171 tests run in ~2 s with no GPU, no Qt, no game, no mouse. |

---

## Phase 1 — Hot-swappable architecture

```
DarkCarnival.exe          ← compiled shell: PySide6 GUI + importlib loader ONLY
scripts/                  ← raw .py business logic (edit/replace at runtime)
  ├── vision.py           Cognitive vision, Russian OCR, template matching
  ├── input_handler.py    Humanised Bezier mouse / Cyrillic-safe typing
  ├── dota_logic.py       HERO_DB (dual-asset), tickets, state machine
  ├── learning.py         Adaptive memory (spatial priors, stats, watchdog)
  └── executor.py         Orchestrator
assets/
  ├── emoji/              pixel-art sprites  → reward screen
  └── portraits/          3D portraits       → hero pick grid
data/                     brain.json, settings.json, logs, script backups
```

### Three ways to ship new logic

1. **Dev Console** — pick a script, paste code, **Apply & Reload**. Live in milliseconds.
2. **GitHub OTA** — configure owner/repo/branch and hit **Sync**; raw `.py` files are
   pulled, validated and hot-reloaded. No rebuild, no reinstall.
3. **Edit the files** on disk and press **Reload ALL**.

Every path is guarded:

* source is `compile()`-checked **before** it is written,
* the previous version is snapshotted (last 10 kept),
* a module that raises on import is **automatically rolled back**,
* reloads purge stale globals, so deleted logic really disappears,
* a partial network failure during OTA never leaves a half-updated folder.

### CI/CD

`.github/workflows/build_exe.yml`:

* **test** (ubuntu): ruff + the full pytest suite — fast, no heavy wheels.
* **build** (windows-latest): PyInstaller compiles the shell, then copies `scripts/`,
  `assets/` and `requirements.txt` **next to** the exe, verifies all of them are present,
  zips it, uploads the artifact and publishes a Release on `v*` tags.

---

## Phase 2 — Cognitive vision (true eyes)

`vision.py` reads the screen with EasyOCR and resolves *meaning*, then returns geometry.

```python
hit = vision.find_intent("accept")      # scans for «Принять»
if hit:
    inputs.click_hit(hit)               # clicks that bounding box, jittered
```

### Russian keyword map

| Intent | Russian |
|---|---|
| Accept | **Принять** |
| Play / Find match | **Найти игру**, **Играть** |
| Continue | **Продолжить** |
| Close | **Закрыть**, **ОК** |
| Victory | **Победа** |
| Safe to leave | **Игру можно безопасно покинуть** |
| Reconnect | **Переподключиться** |
| Leave game | **Покинуть игру** |

…plus the supporting vocabulary for the event loop (`Отменить поиск`, `Против ботов`,
`Тёмный карнавал`, `Поиск`, `Выбрать`, `Готов`, `Забрать награду`, `Игра найдена`,
`Соединение потеряно`).

### Why naive string matching fails here — and what we do instead

Russian captions in this UI overlap heavily at the character level while meaning
opposite things. A plain `difflib` ratio scores **«Покинуть игру» ≈ 0.80 against
«Принять игру»** — high enough to make the agent *abandon* a match it was supposed to
*accept*. Likewise `«ОК»` is a substring of `«ПОКинуть»`, and `«Поиск»` of
`«Отменить поиск»`.

`text_similarity()` therefore matches **token-wise**:

* Latin↔Cyrillic homoglyphs are folded (`Пpинять` → `принять`), as are `ё/е` and case.
* A one-word keyword may sit inside a longer caption (`«Принять 12»` — the accept timer)
  only if it **dominates** ≥ 50 % of what was read.
* A multi-word keyword must align **every** token above a per-token floor, so one shared
  word cannot carry a wrong phrase.
* The raw character fallback is damped unless it is near-identical (≥ 0.88), which
  tolerates OCR merging two words but rejects accept/leave confusion.

Long phrases get a looser threshold than short ones, and the whole table is unit-tested
against realistic OCR corruption.

**Speed:** regions are fractional (resolution independent) so OCR runs on a crop, not the
desktop; `learning.py` remembers where each button was last seen and searches that small
region first.

---

## Ticket economy — 11 Tarot arcana, ×3 only

The event groups rewards into 11 arcana panels (ШУТ, МАГ, ВЕРХОВНАЯ ЖРИЦА, ИМПЕРАТОР,
ВЛЮБЛЁННЫЕ, СИЛА, ОТШЕЛЬНИК, КОЛЕСО ФОРТУНЫ, СМЕРТЬ, ДЬЯВОЛ, ЗВЕЗДА). Each panel has
three sections — heroes worth **1**, **2** or **3** tickets per game.

A ×3 game costs exactly as much time as a ×1 game, so the planner is hard-wired to
`min_yield = 3`: heroes below that are never considered. Lower it only if a given arcana
has no ×3 hero you can play.

Which hero sits in which section **cannot be derived from the pixel-art icons** (35×35 px)
and Valve reshuffles them between event patches — so the mapping is *data*, not code. It
lives in `data/tickets.json` and is edited on the **«Билеты»** tab:

```json
{ "min_yield": 3,
  "arcana": { "death": { "name_ru": "СМЕРТЬ", "x3": ["Фантом Ассасин"], "x2": [], "x1": [] } } }
```

With an empty table the agent refuses to start and says exactly what to fill in — it
never burns games on guesses. `plan_next_pick()` then targets the arcana with the biggest
remaining gap, rotates between equally good heroes, and stops once the goal is met.

## Phase 3 — Dual-asset hero database

The event UI draws heroes **twice**: pixel-art emojis on the ticket/reward screen, and
high-res 3D portraits in the pick grid. `HERO_DB` carries both, plus the Russian
localisation used to drive the search field.

```python
HERO_DB = {
    "phantom_assassin": {
        "name_ru": "Фантом Ассасин",
        "aliases_ru": ["Фантомка", "ФА"],
        "emoji_img": "emoji_pa.png",          # reward-screen analysis
        "portrait_img": "portrait_pa.png",    # pick-grid targeting
        "grants_tickets": ["Death", "Death", "Death"],
        "tier": 3, "role": "carry", "bot_difficulty": "easy",
    },
    ...
}
```

Heroes are resolved **by name**, so all 120+ heroes work without any PNG at all; the
sprites merely make it faster and let the agent visually confirm the reward. Selection is
a three-stage cascade, so a missing or stale sprite never stalls a run:

1. type the Russian name into the search field (clipboard paste — pyautogui cannot type
   Cyrillic),
2. locate the **portrait** by template matching,
3. fall back to **reading** the hero's localised name in the grid with OCR.

A greedy planner (`plan_next_hero`) picks whichever hero closes the most of your
remaining ticket goal, breaking ties toward the faster, lower-tier bot game.

---

## The loop

```
DASHBOARD ─► QUEUEING ─► MATCH_FOUND ─► HERO_PICK ─► IN_GAME
    ▲                                                   │
    └── REWARD_SCREEN ◄── SAFE_TO_LEAVE ◄── POST_GAME ◄──┘

 interrupts: DISCONNECTED («Переподключиться») · UNKNOWN (recovery ladder)
```

State is re-derived from Russian keywords **every tick**, so an unexpected modal is seen
immediately. Transient popups outrank the screens behind them. Each state has a plausible
duration; a watchdog escapes anything that overruns. A failing tick is logged and
retried — it never kills the session.

`learning.py` persists spatial priors, per-intent success rates and cycle history to
`data/brain.json` (atomic writes, schema-versioned, corruption-tolerant).

---

## Install & run

```bash
git clone https://github.com/kellemscoppensxp373-ship-it/dotadarkcarnavalafk
cd dotadarkcarnavalafk
python -m venv .venv && .venv\Scripts\activate      # Python 3.12+
pip install -r requirements.txt
python main.py
```

First launch downloads the EasyOCR Russian model (~100 MB, once).

**Simulation mode is ON by default** — the agent reads and reasons but never touches
your real mouse or keyboard. Verify the log shows the correct Russian words being
detected, then switch it off. **F12 is the panic stop.**

### Build the exe

```bash
pip install pyinstaller
pyinstaller DarkCarnival.spec --noconfirm
# then copy scripts/ and assets/ next to dist/DarkCarnival/DarkCarnival.exe
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

201 tests, ~3 seconds, no game required. The suite includes a scripted **fake Russian
Dota client** (`tests/fake_dota.py`) that renders Cyrillic captions, crops regions like a
real OCR pass, and reacts to synthesised clicks — the agent plays complete cycles against
it in CI. `tests/qt_stub.py` lets the real `MainWindow` be driven headless.

---

## Configuration

`data/settings.json` (also editable in the GUI):

| Key | Meaning |
|---|---|
| `simulate` | `true` = never touch the real mouse/keyboard |
| `ticket_target` | e.g. `{"death": 30, "jester": 12}` — per-arcana goal |
| `min_ticket_yield` | `3` = only play heroes worth 3 tickets |
| `avoid_heroes` | hero names the planner must skip |
| `max_cycles` | `0` = unlimited |
| `match_threshold` | OCR fuzzy-match floor (default `0.78`) |
| `ocr_gpu` | CUDA for EasyOCR |
| `ota_*` | GitHub owner / repo / branch / path / token |

---

## Tuning on your machine

* **Nothing is detected** → *Dev Console* logs a full OCR dump of what the eyes see.
  Dota must be in **borderless/windowed** mode; exclusive fullscreen blocks capture.
* **Wrong clicks** → raise `match_threshold`.
* **Missed buttons** → lower it, or raise `upscale` (small Cyrillic text OCRs poorly).
* **Hero never found** → recapture the portrait PNG at your resolution
  (see `assets/README.md`); the OCR name fallback will cover you meanwhile.

---

## Disclaimer

Automating a game client may violate the Dota 2 / Steam Subscriber Agreement and can get
an account suspended. This project is published as an engineering reference for
cognitive RPA — OCR-driven semantic targeting, hot-swappable plugin architecture and
adaptive automation. **Use at your own risk.**
