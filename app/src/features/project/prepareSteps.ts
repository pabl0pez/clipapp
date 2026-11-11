/** Per-step progress of a prepare job, derived from its overall progress. */

// Mirrors WEIGHTS in engine/src/clipapp_engine/media/prepare.py (the job only reports the total).
const STEPS: [label: string, weight: number][] = [
  ["Audio del micrófono", 0.1],
  ["Forma de onda", 0.05],
  ["Proxy 720p", 0.75],
  ["Miniaturas", 0.1],
];

/** Split an overall progress in [0, 1] into each step's own progress in [0, 1]. */
export function prepareSteps(progress: number): { label: string; value: number }[] {
  let start = 0;
  return STEPS.map(([label, weight]) => {
    // 1e-9 absorbs float drift in the summed weights (else the last step stops at 99%)
    const value = Math.min(1, Math.max(0, (progress - start + 1e-9) / weight));
    start += weight;
    return { label, value };
  });
}
