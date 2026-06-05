# gitStoneLabs documentation style

Color and Mermaid theme reference for the gitStoneLabs docs and diagrams. Keep this consistent across INSTALL.md, README.md, and any future visual assets. The status badges in INSTALL.md use the hex values below.

---

## Color palette

Dark navy base with a cyan glow.

| Role | Hex | Notes |
|---|---|---|
| **Background, deep** | `#0a1428` | Main backdrop. |
| **Background, panel** | `#1a2a44` | Card and node fills in diagrams. |
| **Background, elevated** | `#1f3a5f` | Highlighted sections, hover states. |
| **Primary, cyan glow** | `#00d4ff` | Primary accent. Use for emphasis, borders, accents. |
| **Primary, soft cyan** | `#4dd0e1` | Edges, dividers, secondary accent. |
| **Primary, pale ice** | `#e0f7ff` | Text on dark backgrounds. |
| **Status, success** | `#3fcf8e` | Green-cyan for "ready" and "OK" states. |
| **Status, warning** | `#ffb84d` | Amber for caution (e.g. the +24 V warning box). |
| **Status, error** | `#ff6b6b` | Soft red for failure and blocked states. |
| **Neutral, pure white** | `#ffffff` | Headings, hero text. |

---

## Mermaid theme directive

Paste this at the top of every Mermaid diagram in the repo for visual consistency:

````markdown
```mermaid
%%{init: {'theme':'base','themeVariables':{
  'background':'#0a1428',
  'primaryColor':'#1a2a44',
  'primaryTextColor':'#e0f7ff',
  'primaryBorderColor':'#00d4ff',
  'lineColor':'#4dd0e1',
  'secondaryColor':'#1f3a5f',
  'tertiaryColor':'#163056',
  'noteBkgColor':'#1a2a44',
  'noteTextColor':'#e0f7ff',
  'noteBorderColor':'#00d4ff',
  'edgeLabelBackground':'#0a1428'
}}}%%
flowchart LR
    A[Start] --> B[End]
```
````

For dynamic accents inside a diagram, define classDefs:

```
classDef warn fill:#3a2818,stroke:#ffb84d,color:#ffe0a0;
classDef good fill:#16352b,stroke:#3fcf8e,color:#c0f0d8;
classDef err  fill:#3a1a1a,stroke:#ff6b6b,color:#ffd0d0;
classDef opt  fill:#1f3a5f,stroke:#4dd0e1,color:#e0f7ff,stroke-dasharray:5 5;
```

Then apply with `class B1,B2 opt;` after declaring the nodes.

---

## Typography and tone

- **Voice:** matter-of-fact, technical, no marketing fluff. Headings short.
- **Code samples:** always show the exact path or command, never `path/to/file`, always the real one (e.g. `~/klipper/klippy/extras/`).
- **Hardware references:** brand and chipset together on first mention ("CH341 USB-RS485 adapter"), then chipset alone after.
