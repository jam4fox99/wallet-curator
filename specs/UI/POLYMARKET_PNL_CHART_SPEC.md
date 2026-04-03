# Polymarket Profile P&L Chart - UI Specification

Pixel-perfect reproduction spec for the Polymarket portfolio Profit/Loss chart
as seen on `polymarket.com/@whatyouknowboutlove`.

---

## 1. Overall Layout

The chart lives inside a **card container** within the profile header, to the right
of the user info (avatar, username, stats).

```
+---------------------------------------------------------------+
| [Avatar] whatyouknowboutlove   |  ^ Profit/Loss  1D 1W 1M ALL |
| Joined Dec 2025 - 629 views   |  $38,753.37          Polymarket|
|                                |  All-Time                      |
| $8,167.24   $4,585.69   300   |  ~~~~~~~~chart area~~~~~~~~~~ |
| Pos. Value  Biggest Win  Pred  |                                |
+---------------------------------------------------------------+
```

### Card Container
- **Background**: `#181d21` (rgb 24, 29, 33)
- **Border**: `1px solid #242b32` (rgb 36, 43, 50)
- **Border radius**: `15.2px`
- **Overflow**: hidden

### Page Background
- `#15191d` (rgb 21, 25, 29)

---

## 2. Chart Header Row

Horizontal flex row, `justify-content: space-between`, `align-items: center`.

### Left Side: Title
- Green up-triangle icon (SVG, viewBox `0 0 12 12`, fill `currentColor`)
  - Color: `#7b8996` (secondary text)
- **"Profit/Loss"** heading
  - Font: `14px` Inter, semi-bold
  - Color: `#7b8996` (rgb 123, 137, 150)

### Right Side: Time Period Buttons
Horizontal row, `gap: 6px`.

| Button | State    | Color       | Background    | Font |
|--------|----------|-------------|---------------|------|
| `1D`   | Inactive | `#7b8996`   | transparent   | 14px |
| `1W`   | Inactive | `#7b8996`   | transparent   | 14px |
| `1M`   | Inactive | `#7b8996`   | transparent   | 14px |
| `ALL`  | Active   | `#0093fd`   | transparent   | 14px |

- Border radius on buttons: `9.2px`
- No visible border on buttons

---

## 3. Value Display Row

Flex row, `justify-content: space-between`, `align-items: center`.

### Left Side: Profit Value
- **Dollar amount**: `$38,753.37`
  - Font: **30px**, weight **600** (semi-bold), Inter
  - Color: `#ffffff` (white)
  - Uses `<number-flow-react>` for animated number transitions
- Download icon button to the right of value

### Sub-label
- **"All-Time"**
  - Font: `12px`, Inter
  - Color: `#7b8996`

### Right Side: Branding
- Polymarket logo (small SVG, `~18px` height)
- **"Polymarket"** text
  - Color: `#7b8996`

---

## 4. Chart SVG Specification

### Container
- SVG element, no explicit viewBox set
- Rendered size: **442 x 64px** (visible clip area: **442 x 54px**)
- `fill="none"` on root SVG

### Structure
```xml
<svg>
  <defs>
    <!-- Stroke gradient (the line) -->
    <linearGradient id="strokeGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0093fd" stop-opacity="1" />
      <stop offset="100%" stop-color="#9B51E0" stop-opacity="1" />
    </linearGradient>

    <!-- Fill gradient (area under line) -->
    <linearGradient id="fillGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0093fd" stop-opacity="0.25" />
      <stop offset="100%" stop-color="#9B51E0" stop-opacity="0.005" />
    </linearGradient>

    <!-- Clip path -->
    <clipPath id="chartClip">
      <rect width="442" height="54" />
    </clipPath>
  </defs>

  <g clip-path="url(#chartClip)">
    <!-- Area fill path (closed shape: line + bottom edge) -->
    <path d="..." fill="url(#fillGrad)" stroke-width="0" />

    <!-- Stroke line path -->
    <path d="..." fill="transparent" stroke="url(#strokeGrad)" stroke-width="2" />

    <!-- Invisible interaction rect -->
    <rect width="442" height="54" fill="transparent" />
  </g>
</svg>
```

### Gradient Details
Both gradients are **vertical** (`x1=0 y1=0 x2=0 y2=1`):

| Gradient   | Top Color  | Top Opacity | Bottom Color | Bottom Opacity |
|------------|-----------|-------------|-------------|----------------|
| Stroke     | `#0093fd` | 1.0         | `#9B51E0`   | 1.0            |
| Fill       | `#0093fd` | 0.25        | `#9B51E0`   | 0.005          |

### Path Construction

**Stroke path**: Smooth curve (cubic bezier `C` commands) through all data points.
- `stroke-width="2"`
- `fill="transparent"`
- Uses `url(#strokeGrad)` for stroke

**Fill path**: Same curve as stroke, but closed at the bottom:
- After the last data point, line down to `(maxX, chartHeight)`
- Then line left to `(0, chartHeight)`
- Then close path back to start
- `fill="url(#fillGrad)"`
- `stroke-width="0"`

### Data Points
- See `chart_coordinates.json` for full coordinate data
- 496 original points, sampled to ~160 in the JSON
- **Coordinate system**: X = 0..442 (left to right, time), Y = 0..54 (top = high profit, bottom = low/zero)
- Y is inverted: `minY=6.88` (peak profit) at top, `maxY=50.01` (lowest point) near bottom
- The line starts flat around Y=48.75 (near zero/baseline), dips, then shoots up to Y~6.88 (peak)

### Baseline / Zero Line
- A subtle horizontal reference line appears around Y=48-49 in the chart
- Stroke: `#dee3e7` (rgb 222, 227, 231) — but very faint in dark mode
- No explicit `stroke-dasharray` (solid)

---

## 5. Color Palette

### Dark Theme Colors
| Token               | Hex       | RGB              | Usage                        |
|---------------------|-----------|------------------|------------------------------|
| `bg-page`           | `#15191d` | 21, 25, 29       | Page background              |
| `bg-card`           | `#181d21` | 24, 29, 33       | Chart card background        |
| `surface`           | `#1e2428` | 30, 36, 40       | Surface elements             |
| `border`            | `#242b32` | 36, 43, 50       | Card borders                 |
| `text-primary`      | `#dee3e7` | 222, 227, 231    | Primary text                 |
| `text-white`        | `#ffffff` | 255, 255, 255    | Profit value                 |
| `text-secondary`    | `#7b8996` | 123, 137, 150    | Labels, inactive buttons     |
| `brand-500`         | `#0093fd` | 0, 147, 253      | Active button, gradient top  |
| `gradient-end`      | `#9B51E0` | 155, 81, 224     | Gradient bottom (purple)     |
| `positive`          | `#3db468` | 61, 180, 104     | Green P&L gains              |
| `negative`          | `#ff4d4d` | ~255, 77, 77     | Red P&L losses (estimated)   |

### Gradient CSS
```css
/* Stroke gradient */
background: linear-gradient(180deg, #0093fd 0%, #9B51E0 100%);

/* Fill gradient */
background: linear-gradient(180deg, rgba(0,147,253,0.25) 0%, rgba(155,81,224,0.005) 100%);
```

---

## 6. Typography

| Element        | Size  | Weight | Family         | Color     |
|----------------|-------|--------|----------------|-----------|
| Profit value   | 30px  | 600    | Inter          | `#ffffff` |
| "Profit/Loss"  | 14px  | 600    | Inter          | `#7b8996` |
| Time buttons   | 14px  | 400    | Inter          | varies    |
| "All-Time"     | 12px  | 400    | Inter          | `#7b8996` |
| "Polymarket"   | 12px  | 400    | Inter          | `#7b8996` |

---

## 7. Responsive Behavior

- Chart SVG stretches to fill available width
- Points are recalculated proportionally when container resizes
- Below 400px width, the chart auto-scales
- Time period buttons wrap if needed

---

## 8. Interaction States

### Hover Tooltip (not implemented in static view)
- Vertical crosshair line appears on hover
- Tooltip shows date + P&L value at that point
- Dot indicator on the line at hover point

### Time Period Toggle
- Clicking a period button (1D/1W/1M/ALL) refetches data for that range
- Active button gets `#0093fd` color
- Chart re-renders with smooth transition

---

## 9. Implementation Notes

- The chart uses **cubic bezier curves** (`C` commands in SVG path) for smooth interpolation
- The fill area path reuses the stroke path coordinates, closing at the bottom
- `clip-path` prevents overflow on the fill gradient
- `<number-flow-react>` provides animated number transitions on the profit value
- The chart is rendered client-side from P&L time series data (not SSR)
- Data source: Polymarket API, profile-specific P&L endpoint
