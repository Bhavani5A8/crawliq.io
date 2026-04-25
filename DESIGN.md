# Design System Specification: High-End Dark-Mode SaaS

## 1. Overview & Creative North Star

### Creative North Star: "The Obsidian Architect"
This design system is built for tools that handle high-density data and complex workflows, yet demand the aesthetic precision of a luxury editorial piece. It moves away from the "standard dashboard" by prioritizing tonal depth over structural lines and using aggressive typography to anchor the user’s gaze.

The system rejects the generic "card-on-gray" layout. Instead, it treats the screen as a single, deep architectural space where hierarchy is established through **Luminance Layering** and **Intentional Asymmetry**. By utilizing the tight tracking of Plus Jakarta Sans against the airy utility of Inter, we create a rhythmic tension that feels both high-tech and human-centric.

---

## 2. Colors & Surface Philosophy

The color palette is rooted in a deep, midnight foundation (`#0f131d`) with a high-energy Indigo (`#6366F1`) acting as the kinetic spark.

### The "No-Line" Rule
Traditional UI relies on 1px borders to separate content. In this system, **borders are prohibited for sectioning.** 
- Separate logic through background shifts: Place a `surface_container_low` card atop a `surface` background.
- Use negative space (2x the standard gutter) to imply boundaries.

### Surface Hierarchy & Nesting
Depth is achieved through a stacking logic. As elements "rise" toward the user, they become lighter and more translucent:
- **Level 0 (Base):** `surface_dim` (#0f131d) — The infinite canvas.
- **Level 1 (Sections):** `surface_container` (#1c1f2a) — Large structural areas.
- **Level 2 (Cards/Modules):** `surface_container_high` (#262a35) — Focused content blocks.
- **Level 3 (Popovers/Modals):** `surface_bright` (#353944) — Highest prominence.

### The "Glass & Gradient" Rule
To capture the "Linear" aesthetic, floating elements (Navigation, Tooltips, Floating Action Buttons) must use **Glassmorphism**:
- **Background:** `surface_container_highest` at 70% opacity.
- **Effect:** `backdrop-blur: 20px`.
- **Accent:** Use a subtle linear gradient on primary CTAs (`primary` to `primary_container`) with a 15% saturation boost to give the UI "soul" and a sense of light emission.

---

## 3. Typography: Editorial Authority

We use a dual-typeface system to balance brand character with technical legibility.

*   **Display & Headlines (Plus Jakarta Sans):** These are your "anchors." Set these with **tight letter-spacing (-2% to -4%)** and heavy weights. This creates a compact, professional block of text that feels authoritative.
*   **Body & Labels (Inter):** The workhorse. Inter provides the high-readability required for SaaS data. Maintain generous line heights (1.5x - 1.6x) to contrast against the tight headings.

| Level | Token | Font | Size | Weight | Tracking |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Display** | `display-lg` | Plus Jakarta Sans | 3.5rem | 700 | -0.04em |
| **Headline** | `headline-md` | Plus Jakarta Sans | 1.75rem | 600 | -0.02em |
| **Title** | `title-md` | Inter | 1.125rem | 500 | Normal |
| **Body** | `body-md` | Inter | 0.875rem | 400 | Normal |
| **Label** | `label-sm` | Inter | 0.6875rem | 600 | 0.05em (Caps) |

---

## 4. Elevation & Depth

### The Layering Principle
Do not use shadows to create depth on static cards. Instead, use the **Tonal Stacking** method:
1.  **Lowest:** `surface_container_lowest` (#0a0e18) for recessed areas (e.g., a code block or an inner input well).
2.  **Mid:** `surface` (#0f131d) for the main page.
3.  **High:** `surface_container_low` (#171b26) for primary cards.

### Ambient Shadows
When an element must float (Modals, Context Menus), use "Ambient Light" shadows:
- **Color:** `#000000` at 40% opacity OR a tinted version of `on_surface` at 8% opacity.
- **Spread:** `0px 20px 40px -12px`. The shadow should feel like a soft glow of dark air, not a harsh drop-shadow.

### The "Ghost Border" Fallback
If contrast is insufficient (e.g., in accessibility audits), use a **Ghost Border**:
- **Stroke:** 1px solid.
- **Color:** `outline_variant` (#464554) at **15% opacity**.
- **Result:** A border that is felt rather than seen.

---

## 5. Components

### Buttons (Kinetic Indigo)
- **Primary:** Gradient fill (`primary_container` to `primary`). 12px rounded corners. White text (`on_primary`).
- **Secondary:** Surface-only. `surface_container_highest` background with a `Ghost Border`.
- **Tertiary:** Text-only in `primary` color, no background, 4px hover state padding.

### Cards & Lists
- **Rule:** Forbid divider lines. 
- **Separation:** Use `md` (0.75rem) or `lg` (1rem) spacing between list items. 
- **Interaction:** On hover, a card should shift from `surface_container_high` to `surface_bright` with a 200ms ease-in-out transition.

### Input Fields
- **Background:** `surface_container_lowest`.
- **Border:** `none`.
- **Focus State:** A 2px outer glow (not a border) using the `primary` color at 30% opacity. 
- **Typography:** Placeholder text must use `on_surface_variant` at 50% opacity.

### Featured Component: The "Bento" Grid
To emulate the high-end SaaS feel, use asymmetrical grid layouts. Combine one `xl` (1.5rem) rounded card with two `md` (0.75rem) cards to break the visual monotony of a standard 3-column layout.

---

## 6. Do's and Don'ts

### Do
*   **DO** use whitespace as a structural element. If you think there’s enough space, add 16px more.
*   **DO** use `tertiary` (#ffb783) for small, high-attention callouts (like "New" badges or "Sale" tags).
*   **DO** apply `xl` (1.5rem) corner radii to large hero containers and `md` (0.75rem) to smaller nested inputs.

### Don't
*   **DON'T** use pure black (#000000) for backgrounds; it kills the "depth" of the indigo-tinted shadows.
*   **DON'T** use 100% opaque borders. It makes the UI feel like a legacy enterprise app.
*   **DON'T** use standard blue for links. Use the `primary` Indigo (#c0c1ff) for a custom, branded feel.
*   **DON'T** center-align long-form body text. Keep the "Editorial" feel with strict left-alignment and generous margins.