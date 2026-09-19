export function liveInstanceScale(shape, axes) {
  if (shape !== 'capsule') return [...axes];
  const radius = axes[1];
  return [radius, radius, axes[0] + radius];
}

export function reserveInstance(counts, key, maximum) {
  const index = counts.get(key) || 0;
  if (index >= maximum) return -1;
  counts.set(key, index + 1);
  return index;
}

export function machineLayoutSignature(layout) {
  return JSON.stringify([
    layout.belt_len,
    layout.belt_w,
    layout.belt_z,
    layout.cam_x,
    layout.ej_x,
    layout.ej_z_offset,
    layout.n_nozzles,
    layout.split_x,
    layout.split_z_drop,
  ]);
}
