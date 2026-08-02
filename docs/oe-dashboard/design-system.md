# Options Engine Terminal — Design System Spec
**Status:** LOCKED (Phase 2 CLOSED — 2026-07-31)

---

## Color Palette

### Background layers
| Token | HSL | Hex (approx) | Usage |
|---|---|---|---|
| `--background` | `222 45% 6%` | `#090f1a` | Page background |
| `--card` | `220 40% 8%` | `#0d1520` | Section containers |
| `--sidebar` | `222 45% 5%` | `#070c16` | Sidebar panel |

Not pure black. Deep navy-charcoal with consistent hue (220–222°).

### Foreground / text
| Token | HSL | Usage |
|---|---|---|
| `--foreground` | `200 20% 92%` | Primary text |
| `--muted-foreground` | `200 15% 65%` | Labels, secondary text |

### Border / divider
| Token | HSL | Usage |
|---|---|---|
| `--border` | `215 25% 18%` | Hairline table/section dividers |
| `--sidebar-border` | `215 25% 15%` | Sidebar internal dividers |

No card shadows in page components. Borders only.

---

## Two-Color Accent System (approved 2026-07-31)

These are the **only two accent colors** in the terminal. They serve different semantic categories and are not decorative.

### Cyan — live data and value changes
| Property | Value |
|---|---|
| Token | `--primary` / `--accent` |
| HSL | `191 100% 50%` |
| Hex (approx) | `#00d9ff` |
| Class | `text-primary`, `bg-primary`, `border-primary` |

**Where used:**
- Confidence score column in all data tables (`text-primary`)
- Chain hash and sequence number display (`text-primary`)
- Live feed pulse dot in Live Decisions header (`bg-primary animate-pulse`)
- "Why Trade" navigation links (`text-primary hover:text-primary/80`)
- Key calibration metric values (`text-primary`)
- Primary accent for interactive elements (buttons, focus rings)

**Rule:** Cyan = live data values and data-state changes. Not for status or health.

### Green — verified / healthy status
| Property | Value |
|---|---|
| Token | `--chart-2` |
| HSL | `142 76% 36%` |
| Hex (approx) | `#1a9e4a` |
| Class | `bg-chart-2` |

**Where used:**
- Sidebar chain-verified pulse dot when `total_entries > 0` and `last_entry_hash` is non-empty

**Rule:** Green = chain/verification health is confirmed OK.

### Red — failed / unverified status
| Property | Value |
|---|---|
| Token | `--destructive` |
| HSL | `0 84% 60%` |
| Class | `bg-destructive`, `text-destructive` |

**Where used:**
- Sidebar chain dot when endpoint errors, `total_entries = 0`, or `last_entry_hash` is empty
- Error/rejection badges

**Rule:** Red = verification failure or health check failure. Accompanies the green as the failure pair.

### Neutral — loading / unknown status
| Class | Usage |
|---|---|
| `bg-muted animate-pulse` | Sidebar chain dot while chain status fetch is in-flight |

---

## Typography

### Sans-serif (body and UI)
- **Family:** Space Grotesk (Google Fonts, loaded via `index.css` `@import`)
- **Class:** `font-sans` (default body)
- **Weights:** 400, 500, 600, 700

### Monospace (all numeric data)
- **Family:** JetBrains Mono (Google Fonts, loaded via `index.css` `@import`)
- **Token:** `--app-font-mono`
- **Class:** `font-mono`
- **Feature settings:** `'tnum' 1, 'zero' 1` (tabular numbers, slashed zero)
- **Numeric variant:** `tabular-nums`
- **Applied to:** All table cells containing prices, scores, hashes, trace IDs, timestamps

**Rule:** Any value that changes over time or must align in a column uses `font-mono`. UI labels and prose use `font-sans`.

---

## Layout Density

- Nav items: `px-3 py-2` (compact)
- Table cells: `text-xs` throughout
- Section spacing: `space-y-1` in nav, `p-3`/`p-4` in content panels
- Border radius: `--radius: 0.25rem` (minimal rounding)
- No card `drop-shadow` or `shadow-*` utilities in page components

---

## Chain-Verified Dot — States (sidebar footer)

| State | Dot color | Label | Label style | Condition |
|---|---|---|---|---|
| Loading | `bg-muted animate-pulse` | `Chain Loading…` | `text-muted-foreground` | Fetch in-flight, no error |
| Verified | `bg-chart-2 animate-pulse` | `Chain Verified` | `text-muted-foreground` | `total_entries > 0` AND `last_entry_hash` non-empty |
| Unverified | `bg-destructive animate-pulse` | `Chain UNVERIFIED` | `text-destructive font-semibold` | Endpoint error OR `total_entries = 0` OR empty hash |

Fetches `/admin/evidence-chain/status` every 60 seconds. Silent retry disabled — errors immediately flip to red.

---

## What Is Explicitly Not Present

- No card box shadows (`shadow-*`) on page-level containers
- No amber/orange accent (reserved if a "warning" state is needed — not yet defined)
- No gradient backgrounds
- No Inter font (present in `index.html` as a scaffold leftover; overridden by `index.css` imports)
