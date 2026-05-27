
const W = 900;
const H = 200;
const BL = 130;

function nsr(x: number, bl = BL, ra = 90, prLen = 32, stElev = 0): string {
  const pe = x + 18;
  const qs = pe + prLen;
  const rp = qs + 6;
  const se = qs + 12;
  const stEnd = se + 22;
  const tp = se + 38;
  const te = se + 58;
  return [
    `L ${x},${bl}`,
    `C ${x + 4},${bl} ${x + 7},${bl - 20} ${x + 9},${bl - 20}`,
    `C ${x + 11},${bl - 20} ${x + 14},${bl} ${pe},${bl}`,
    `L ${qs},${bl}`,
    `L ${qs + 2},${bl + 8}`,
    `L ${rp},${bl - ra}`,
    `L ${qs + 10},${bl + 12}`,
    `L ${se},${bl}`,
    `L ${stEnd},${bl - stElev}`,
    `C ${stEnd + 7},${bl - stElev} ${tp - 3},${bl - stElev - 22} ${tp},${bl - stElev - 22}`,
    `C ${tp + 3},${bl - stElev - 22} ${te - 5},${bl} ${te},${bl}`,
  ].join(" ");
}

function wideQRS(x: number, bl = BL, amplitude = 75, width = 45): string {
  return [
    `L ${x},${bl}`,
    `L ${x + 6},${bl + 12}`,
    `C ${x + 12},${bl + 12} ${x + 16},${bl - amplitude} ${x + Math.floor(width / 2)},${bl - amplitude}`,
    `C ${x + width - 10},${bl - amplitude} ${x + width - 5},${bl + 16} ${x + width},${bl + 16}`,
    `L ${x + width + 8},${bl}`,
  ].join(" ");
}

function pWave(x: number, bl = BL): string {
  return [
    `L ${x},${bl}`,
    `C ${x + 4},${bl} ${x + 7},${bl - 18} ${x + 9},${bl - 18}`,
    `C ${x + 11},${bl - 18} ${x + 14},${bl} ${x + 18},${bl}`,
  ].join(" ");
}

function sawtooth(x: number, count: number, bl = BL): string {
  let d = `L ${x},${bl}`;
  for (let i = 0; i < count; i++) {
    const ox = x + i * 30;
    d += ` L ${ox + 22},${bl - 24} L ${ox + 30},${bl}`;
  }
  return d;
}

function fibBaseline(x: number, length: number, bl = BL): string {
  const seed = [0, 6, -4, 9, -7, 3, -10, 5, -3, 8, -6, 2, -8, 4, 0, 7, -5, 10, -2, 6];
  let d = `L ${x},${bl}`;
  const step = length / seed.length;
  for (let i = 0; i < seed.length; i++) {
    const cx = x + i * step;
    const cy = bl + seed[i];
    const nx = x + (i + 1) * step;
    const ny = bl + (seed[(i + 1) % seed.length] * 0.6);
    d += ` C ${cx + step * 0.3},${cy} ${nx - step * 0.3},${ny} ${nx},${ny}`;
  }
  return d;
}

function vfibPath(): string {
  const pts: [number, number][] = [];
  let x = 10;
  let y = BL;
  const noise = [20, -35, 55, -18, 40, -60, 25, -42, 30, -50, 38, -22, 48, -65, 18,
    -30, 45, -55, 22, -38, 50, -28, 35, -48, 20, -32, 42, -58, 28, -40,
    52, -18, 38, -62, 24, -44, 46, -20, 34, -56, 26, -36, 48, -64, 22, -42,
    40, -24, 50, -52, 30, -38, 44, -60, 20, -28, 46, -48, 32, -66, 28, -34,
    54, -22, 36, -58, 42, -16, 30, -50, 44, -34, 52, -60, 18, -40, 38, -54,
    24, -30, 46, -68, 22, -44, 48, -20, 34, -62, 26];
  let d = `M 0,${BL}`;
  let xi = 10;
  for (let i = 0; i < noise.length; i++) {
    const step = 4 + Math.abs(noise[i]) / 12;
    xi += step;
    if (xi > W - 10) break;
    y = BL + noise[i];
    pts.push([xi, y]);
  }
  for (const [px, py] of pts) {
    d += ` L ${px},${py}`;
  }
  d += ` L ${W},${BL}`;
  return d;
}

const RHYTHM_CONFIGS: Record<string, {
  label: string;
  rate: string;
  features: string[];
  buildPath: () => string;
}> = {
  normal: {
    label: "Normal Sinus Rhythm",
    rate: "Rate: 60–100 bpm",
    features: ["Regular P waves before each QRS", "Normal PR interval (0.12–0.20s)", "Narrow QRS < 0.12s"],
    buildPath: () => {
      const RR = 160;
      let d = `M 0,${BL} L 40,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(40 + i * RR);
        if (i < 4) d += ` L ${40 + i * RR + 120},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  bradycardia: {
    label: "Sinus Bradycardia",
    rate: "Rate: < 60 bpm",
    features: ["Regular P waves before each QRS", "Prolonged RR interval", "Normal P-QRS-T morphology"],
    buildPath: () => {
      const RR = 280;
      let d = `M 0,${BL} L 20,${BL}`;
      for (let i = 0; i < 3; i++) {
        d += nsr(20 + i * RR);
        if (i < 2) d += ` L ${20 + i * RR + 120},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  tachycardia: {
    label: "Sinus Tachycardia",
    rate: "Rate: > 100 bpm",
    features: ["P wave before each QRS (may merge with T)", "Short RR interval", "Normal QRS morphology"],
    buildPath: () => {
      const RR = 96;
      let d = `M 0,${BL} L 20,${BL}`;
      for (let i = 0; i < 8; i++) {
        d += nsr(20 + i * RR, BL, 80, 24);
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  afib: {
    label: "Atrial Fibrillation",
    rate: "Rate: 100–175 bpm (irregular)",
    features: ["No distinct P waves — fibrillatory baseline", "Irregularly irregular RR intervals", "Narrow QRS (unless aberrant)"],
    buildPath: () => {
      const qrsOffsets = [50, 150, 280, 370, 490, 600, 700, 810];
      let d = `M 0,${BL}`;
      d += fibBaseline(0, qrsOffsets[0], BL);
      for (let i = 0; i < qrsOffsets.length; i++) {
        const xq = qrsOffsets[i];
        const nextX = qrsOffsets[i + 1] ?? W;
        const se = xq + 8;
        d += ` L ${xq},${BL} L ${xq + 2},${BL + 7} L ${xq + 5},${BL - 72} L ${xq + 8},${BL + 10} L ${se},${BL}`;
        d += fibBaseline(se, nextX - se, BL);
      }
      return d;
    },
  },
  flutter: {
    label: "Atrial Flutter",
    rate: "Atrial: 300 bpm  |  Ventricular: 150 bpm",
    features: ["Regular sawtooth P waves at 300 bpm", "2:1 AV block (QRS every other flutter wave)", "Narrow QRS"],
    buildPath: () => {
      const qrsOffsets = [80, 140, 200, 260, 320, 380, 440, 500, 560, 620, 680, 740, 800];
      let d = `M 0,${BL}`;
      d += sawtooth(0, 30, BL);
      for (let i = 0; i < qrsOffsets.length; i++) {
        if (i % 2 !== 0) continue;
        const xq = qrsOffsets[i];
        d += ` L ${xq},${BL} L ${xq + 2},${BL + 7} L ${xq + 5},${BL - 70} L ${xq + 8},${BL + 10} L ${xq + 14},${BL}`;
        d += sawtooth(xq + 14, 2, BL);
      }
      return d;
    },
  },
  svt: {
    label: "Supraventricular Tachycardia (SVT)",
    rate: "Rate: 150–250 bpm",
    features: ["Very fast, regular rhythm", "Narrow QRS", "P waves hidden in or after QRS"],
    buildPath: () => {
      const RR = 72;
      let d = `M 0,${BL} L 15,${BL}`;
      for (let i = 0; i < 12; i++) {
        const x = 15 + i * RR;
        const se = x + 12;
        d += ` L ${x},${BL} L ${x + 2},${BL + 7} L ${x + 5},${BL - 72} L ${x + 8},${BL + 10} L ${se},${BL}`;
        d += ` C ${se + 8},${BL} ${se + 16},${BL - 10} ${se + 24},${BL - 10} C ${se + 32},${BL - 10} ${se + 38},${BL} ${se + 40},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  pvcs: {
    label: "Premature Ventricular Contractions (PVCs)",
    rate: "Rate: 60–80 bpm (with ectopic beats)",
    features: ["Wide, bizarre QRS complexes", "No preceding P wave for PVC", "Compensatory pause follows PVC"],
    buildPath: () => {
      const pvcAt = [2, 5];
      let d = `M 0,${BL} L 30,${BL}`;
      let x = 30;
      for (let i = 0; i < 7; i++) {
        if (pvcAt.includes(i)) {
          d += wideQRS(x, BL, 82, 48);
          x += 120;
        } else {
          d += nsr(x);
          x += 158;
        }
        d += ` L ${x},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  vtach: {
    label: "Ventricular Tachycardia (VTach)",
    rate: "Rate: 100–250 bpm",
    features: ["Wide, bizarre QRS (> 0.12s)", "No discernible P waves", "Regular rhythm"],
    buildPath: () => {
      const RR = 88;
      let d = `M 0,${BL} L 18,${BL}`;
      for (let i = 0; i < 10; i++) {
        const x = 18 + i * RR;
        d += wideQRS(x, BL, 80, 48);
        d += ` L ${x + 56},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  vfib: {
    label: "Ventricular Fibrillation (VFib)",
    rate: "No effective rate",
    features: ["Chaotic, irregular waveform", "No organized P waves or QRS", "No cardiac output — EMERGENCY"],
    buildPath: vfibPath,
  },
  block1: {
    label: "First Degree AV Block",
    rate: "Rate: 60–100 bpm",
    features: ["PR interval > 0.20s (> 40px)", "Every P wave followed by QRS", "Normal QRS morphology"],
    buildPath: () => {
      const RR = 168;
      let d = `M 0,${BL} L 30,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(30 + i * RR, BL, 88, 60);
        if (i < 4) d += ` L ${30 + i * RR + 136},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  block3: {
    label: "Third Degree (Complete) AV Block",
    rate: "Ventricular rate: 20–40 bpm  |  Atrial rate: 60–100 bpm",
    features: ["P waves and QRS are completely independent", "Wide, slow ventricular escape rhythm", "No association between P and QRS"],
    buildPath: () => {
      let d = `M 0,${BL} L 10,${BL}`;
      const pWaveOffsets = [10, 80, 150, 220, 290, 360, 430, 500, 570, 640, 710, 780, 850];
      const qrsOffsets = [50, 280, 510, 740];
      let i = 0;
      let pi = 0;
      const events: Array<{ x: number; type: "p" | "q" }> = [
        ...pWaveOffsets.map(x => ({ x, type: "p" as const })),
        ...qrsOffsets.map(x => ({ x, type: "q" as const })),
      ].sort((a, b) => a.x - b.x);

      let cx = 10;
      for (const ev of events) {
        if (ev.x > W - 10) break;
        d += ` L ${ev.x},${BL}`;
        if (ev.type === "p") {
          d += pWave(ev.x, BL);
          cx = ev.x + 18;
        } else {
          d += wideQRS(ev.x, BL, 76, 50);
          cx = ev.x + 58;
        }
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  stemi: {
    label: "ST Elevation (STEMI)",
    rate: "Rate: 60–100 bpm",
    features: ["ST segment elevated > 1mm (2px/mm)", "Indicates acute myocardial infarction", "ST elevation in contiguous leads"],
    buildPath: () => {
      const RR = 160;
      let d = `M 0,${BL} L 40,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(40 + i * RR, BL, 90, 32, 22);
        if (i < 4) d += ` L ${40 + i * RR + 120},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
};

const RHYTHM_ALIASES: Record<string, string> = {
  "normal-sinus": "normal",
  "sinus-bradycardia": "bradycardia",
  "sinus-tachycardia": "tachycardia",
  "atrial-fibrillation": "afib",
  "atrial-flutter": "flutter",
  "ventricular-tachycardia": "vtach",
  "ventricular-fibrillation": "vfib",
  "heart-block-1": "block1",
  "heart-block-3": "block3",
  "complete-heart-block": "block3",
  "first-degree-block": "block1",
  "third-degree-block": "block3",
};

export default function EkgDisplay({ rhythm }: { rhythm: string }) {
  const key = RHYTHM_ALIASES[rhythm] ?? rhythm;
  const config = RHYTHM_CONFIGS[key];

  if (!config) {
    return (
      <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 text-yellow-700 text-sm mb-6">
        EKG strip unavailable for rhythm: <code>{rhythm}</code>
      </div>
    );
  }

  const path = config.buildPath();

  return (
    <div className="mb-8">
      <div className="rounded-xl overflow-hidden border border-slate-200 shadow-sm bg-white">
        <div className="bg-slate-700 px-4 py-2 flex items-center gap-2">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-400" />
            <div className="w-3 h-3 rounded-full bg-yellow-400" />
            <div className="w-3 h-3 rounded-full bg-green-400" />
          </div>
          <span className="text-slate-200 text-xs font-mono ml-2">ECG Monitor — Lead II</span>
        </div>

        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          style={{ background: "#fffaf5" }}
          aria-label={`ECG strip showing ${config.label}`}
        >
          <defs>
            <pattern id={`smallgrid-${key}`} width="8" height="8" patternUnits="userSpaceOnUse">
              <path d="M 8 0 L 0 0 0 8" fill="none" stroke="#ffd0c4" strokeWidth="0.3" />
            </pattern>
            <pattern id={`grid-${key}`} width="40" height="40" patternUnits="userSpaceOnUse">
              <rect width="40" height="40" fill={`url(#smallgrid-${key})`} />
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#ffb0a0" strokeWidth="0.7" />
            </pattern>
          </defs>
          <rect width={W} height={H} fill={`url(#grid-${key})`} />
          <line x1="0" y1={BL} x2={W} y2={BL} stroke="#ffb0a0" strokeWidth="0.5" strokeDasharray="4 4" />
          <path
            d={path}
            fill="none"
            stroke="#111"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      <div className="mt-3 px-1">
        <div className="flex flex-wrap gap-2">
          {config.features.map((f) => (
            <span
              key={f}
              className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-100 font-medium"
            >
              {f}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
