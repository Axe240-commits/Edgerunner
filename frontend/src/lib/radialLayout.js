// Compute radial positions for 13 feature-group nodes around center
// Returns array of { id, name, color, features, x, y, angle }

import GROUPS from './featureGroups'

export function computeRadialPositions(cx, cy, radius) {
  const count = GROUPS.length
  return GROUPS.map((g, i) => {
    const angle = (i / count) * Math.PI * 2 - Math.PI / 2 // start from top
    return {
      ...g,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
      angle,
    }
  })
}
