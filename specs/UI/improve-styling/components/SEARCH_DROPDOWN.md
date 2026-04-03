# Polymarket Search Dropdown - UI Specification

Pixel-perfect reproduction spec for the Polymarket search input and dropdown menu
as seen on any Polymarket page (global navigation bar).

---

## 1. Overall Structure

```
+--[ Search Input ]------------------------------------+
| Q  Search polymarkets...                              |
+-------------------------------------------------------+
| BROWSE                                                |
| [New] [Trending] [Popular] [Liquid] [Ending Soon]    |
| [Competitive]                                         |
|                                                       |
| RECENT                                                |
| [icon] Counter-Strike: ASTRAL vs megoshort...    x    |
| [icon] LoL: G2 NORD vs Eintracht Spandau...     x    |
| [icon] Counter-Strike: G2 vs BetBoom Team...     x    |
| [icon] Counter-Strike: ECSTATIC vs Ursa...       x    |
| [icon] Counter-Strike: AaB Esport vs Base...     x    |
+-------------------------------------------------------+
```

The dropdown appears directly below the search input with no gap, sharing
the same width. Top corners are square (flush with input), bottom corners
are rounded.

---

## 2. Search Input

### Container
- **Position**: `relative`
- **Width**: `600px` (desktop), `min-width: 400px`
- **Max-width**: `600px`

### Input Field
- **Background**: `#1e2428` (rgb 30, 36, 40)
- **Color**: `#e5e5e5` (rgb 229, 229, 229)
- **Font**: `14px`, weight `400`, Inter
- **Border**: `1px solid transparent`
- **Border radius**: `9.2px` (collapses to `9.2px 9.2px 0 0` when dropdown open)
- **Padding**: `4px 12px 4px 44px` (left padding accommodates search icon)
- **Height**: `40px`
- **Placeholder**: `"Search polymarkets..."`
- **Caret color**: `#e5e5e5`

### Focus State
- **Border color**: remains transparent
- **Box shadow**: subtle blue glow (`rgba(0, 147, 253, 0.7)` based ring)
- **Background**: unchanged (`#1e2428`)

### Search Icon (magnifying glass)
- **Size**: `18px x 18px`
- **Color**: `#7b8996` (rgb 123, 137, 150)
- **Position**: `absolute`, `left: 16px`, vertically centered

---

## 3. Dropdown Panel

### Outer Wrapper
- **Position**: `absolute`
- **Top**: `40px` (flush below input)
- **Left**: `0`
- **Width**: `600px` (matches input)
- **Z-index**: `50`

### Inner Card
- **Background**: `#15191d` (rgb 21, 25, 29)
- **Border**: `1px solid #242b32` (left, right, bottom only — `border-t-0`)
- **Border radius**: `0 0 11.2px 11.2px` (square top, rounded bottom)
- **Box shadow**: `rgba(0, 0, 0, 0.06) 0px 8px 16px 0px`
- **Padding**: `12px 8px 8px`
- **Overflow**: `hidden`
- **Display**: `flex`, `flex-direction: column`

---

## 4. Browse Section

### Section Label ("BROWSE")
- **Element**: `<p>`
- **Text**: `"Browse"` (rendered uppercase via CSS)
- **Font size**: `10px`
- **Font weight**: `500`
- **Color**: `#7b8996` (rgb 123, 137, 150)
- **Letter spacing**: `0.5px`
- **Text transform**: `uppercase`
- **Line height**: `15px`
- **Padding**: `0`

### Pill Container
- **Display**: `flex`
- **Flex wrap**: `wrap`
- **Gap**: `8px`
- **Padding**: `0`
- **Parent section padding**: `8px`

### Browse Pills (New, Trending, Popular, Liquid, Ending Soon, Competitive)
- **Element**: `<a>` wrapping a `<div>`
- **Background**: `transparent`
- **Color**: `#e5e5e5` (rgb 229, 229, 229)
- **Font size**: `16px`
- **Font weight**: `400`
- **Border**: `1px solid #242b32` (rgb 36, 43, 50)
- **Border radius**: `11.2px`
- **Padding**: `6px 12px 6px 10px`
- **Height**: `34px`
- **Display**: `flex`
- **Align items**: `center`
- **Gap**: `6px` (between icon and text)
- **Cursor**: `pointer`

### Pill Hover State
- **Background**: `neutral-50` (Tailwind — approximately `rgba(255, 255, 255, 0.05)` in dark mode)

### Pill Icon (left of text)
- **Size**: `16px x 16px` (SVG)
- **Color**: inherits from pill text color

---

## 5. Recent Section

### Section Label ("RECENT")
- **Element**: `<p>`
- **Text**: `"Recent"` (rendered uppercase via CSS)
- **Font size**: `10px`
- **Font weight**: `500`
- **Color**: `#7b8996` (rgb 123, 137, 150)
- **Letter spacing**: `0.5px`
- **Text transform**: `uppercase`
- **Padding**: `0 0 0 8px`

### List Container
- **Display**: `flex`
- **Flex direction**: `column`
- **Gap**: `0` (items are flush, no gap between them)

### Recent Item Row
- **Element**: `<a>` wrapping a `<div>`
- **Background**: `transparent`
- **Border radius**: `7.2px`
- **Padding**: `8px`
- **Height**: `48px`
- **Display**: `flex`
- **Align items**: `center`
- **Justify content**: `space-between`
- **Gap**: `16px`
- **Cursor**: `pointer`
- **Transition**: `all`

### Recent Item Hover State
- **Background**: `neutral-50` (approx `rgba(255, 255, 255, 0.05)` in dark mode)
- CSS class: `hover:bg-neutral-50`

### Recent Item Selected State
- **Background**: `neutral-100` (approx `rgba(255, 255, 255, 0.08)` in dark mode)
- CSS class: `data-[selected=true]:bg-neutral-100`

### Left Side (icon + text)
- **Display**: `flex`
- **Gap**: `8px`
- **Align items**: `center`

### Market Icon
- **Element**: `<img>`
- **Width**: `30px`
- **Height**: `30px`
- **Border radius**: `0`
- **Object fit**: `cover`

### Market Name Text
- **Element**: `<p>`
- **Font size**: `14px`
- **Font weight**: `500`
- **Color**: `#e5e5e5` (rgb 229, 229, 229)
- **Line height**: `20px`
- **Overflow**: `hidden`
- **Line clamp**: `1` (single line, truncated)

### Dismiss Button (X)
- **Element**: `<button>`
- **Padding**: `4px`
- **Width**: `20px` (4px padding + 12px icon)
- **Height**: `20px`
- **Color**: `#7b8996` (rgb 123, 137, 150) — secondary text
- **Cursor**: `pointer`
- **Flex shrink**: `0` (doesn't compress)

### Dismiss Icon (X SVG)
- **Width**: `12px`
- **Height**: `12px`
- **Stroke**: `currentColor`

---

## 6. Color Palette (dropdown-specific)

| Token | Hex | RGB | Usage |
|---|---|---|---|
| `input-bg` | `#1e2428` | 30, 36, 40 | Search input background |
| `dropdown-bg` | `#15191d` | 21, 25, 29 | Dropdown panel background |
| `border` | `#242b32` | 36, 43, 50 | Input border (focus), dropdown border, pill borders |
| `text-primary` | `#e5e5e5` | 229, 229, 229 | Input text, pill text, market names |
| `text-secondary` | `#7b8996` | 123, 137, 150 | Search icon, section labels, dismiss button |
| `hover-bg` | ~`rgba(255,255,255,0.05)` | — | Row/pill hover background |
| `selected-bg` | ~`rgba(255,255,255,0.08)` | — | Keyboard-selected row background |

---

## 7. Border Radius Reference

| Element | Radius |
|---|---|
| Search input (normal) | `9.2px` |
| Search input (dropdown open) | `9.2px 9.2px 0 0` |
| Dropdown panel | `0 0 11.2px 11.2px` |
| Browse pills | `11.2px` |
| Recent item rows | `7.2px` |

---

## 8. Typography Summary

| Element | Size | Weight | Color | Extra |
|---|---|---|---|---|
| Input text | 14px | 400 | `#e5e5e5` | — |
| Input placeholder | 14px | 400 | `#e5e5e5` | — |
| Section labels | 10px | 500 | `#7b8996` | uppercase, letter-spacing 0.5px |
| Browse pill text | 16px | 400 | `#e5e5e5` | — |
| Recent item text | 14px | 500 | `#e5e5e5` | line-clamp 1, overflow hidden |

---

## 9. Spacing Summary

| Element | Value |
|---|---|
| Input padding (left, for icon) | `44px` |
| Input padding (right) | `12px` |
| Dropdown inner padding | `12px 8px 8px` |
| Browse section padding | `8px` |
| Browse pill padding | `6px 12px 6px 10px` |
| Browse pill gap (icon-to-text) | `6px` |
| Browse pills gap (between pills) | `8px` |
| Recent item row padding | `8px` |
| Recent item row gap (text-to-dismiss) | `16px` |
| Recent left side gap (icon-to-text) | `8px` |

---

## 10. Implementation Notes

- The dropdown uses Tailwind CSS classes (`hover:bg-neutral-50`, `data-[selected=true]:bg-neutral-100`)
- The search input and dropdown share the same width, creating a unified container look
- Browse pills are `<a>` elements linking to Polymarket prediction pages with sort params
- Recent items use `line-clamp: 1` for text truncation (CSS `display: -webkit-box`)
- The dismiss button uses `shrink-0` to prevent compression when text is long
- Keyboard navigation is supported via `data-selected` attribute on recent items
- The dropdown panel uses `border-t-0` so there's no double border between input and panel
- Shadow is subtle: only a downward 8px blur at 6% black opacity
