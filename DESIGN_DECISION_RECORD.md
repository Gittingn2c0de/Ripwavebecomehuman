# Ripwave Become Human spatial laboratory redesign

## Decision

The persistent left rail is removed. The application now uses a full-width
clinical laboratory workspace with the live field as its visual instrument and
contextual controls in the topbar and workspace layer.

## Rationale

- The field and MuJoCo frame are the primary evidence, so they receive the
  broadest visual area and the strongest contrast.
- The rail contained only Studio navigation and an Event trace shortcut.
  Studio is now the page itself; Event trace is a labelled topbar action.
- Existing cards remain available as movable spatial nodes instead of being
  duplicated into a second navigation system.
- The workspace controller owns only layout transforms, grouping, threads,
  recentering, and reduced-motion behavior. Python, Ripwave state, and MuJoCo
  remain the source of truth for simulation.

## Spatial grammar

- Clinical porcelain, cool grey grid lines, graphite command/status surfaces,
  and the existing lime/cyan/teal signal colors form the visual token system.
- The central dark stage is the instrument chamber. White surrounding surfaces
  provide measurement context rather than a generic dashboard background.
- Threads are limited to real UI/data relationships: field-to-medium,
  field-to-input, and field-to-simulation.
- Nodes can be dragged, grouped by proximity, detached by pulling apart, and
  recentered. Essential controls remain in the DOM and do not recede away.

## Navigation and accessibility

- Event trace is reachable from the topbar and remains directly reachable by
  normal document scrolling.
- Controls inside nodes are excluded from drag initiation.
- Reduced motion disables layout drift and preserves readable static placement.
- At narrow widths, the spatial overlay and transforms are disabled and the
  existing stacked layout becomes the primary navigation order.

## Collaboration record

The local BEAST, FORMA x STORYTELLER, and CANVAS documents were inspected.
No separately addressable Gems, design-review agents, or Canvas visual
workspace were available in this environment, so no external consultation is
claimed. Their applicable principles were synthesized locally: state and trust
from BEAST, meaningful physically restrained motion from FORMA, and value/grid/
figure-ground discipline from CANVAS.
