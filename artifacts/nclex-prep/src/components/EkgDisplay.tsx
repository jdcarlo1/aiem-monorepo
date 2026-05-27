const W = 1000;
const H = 240;
const BL = 155;
const SMALL = 10;
const LARGE = 50;

function nsr(x: number, bl = BL, ra = 95, prLen = 34, stElev = 0): string {
  const pe = x + 20;
  const qs = pe + prLen;
  const qEnd = qs + 4;
  const rp = qEnd + 4;
  const sEnd = rp + 5;
  const se = sEnd + 4;
  const stEnd = se + 24;
  const tp = stEnd + 14;
  const te = stEnd + 40;
  return [
    `L ${x},${bl}`,
    `C ${x + 5},${bl} ${x + 8},${bl - 14} ${x + 10},${bl - 14}`,
    `C ${x + 12},${bl - 14} ${x + 16},${bl} ${pe},${bl}`,
    `L ${qs},${bl}`,
    `L ${qEnd},${bl + 5}`,
    `L ${rp},${bl - ra}`,
    `L ${sEnd},${bl + 8}`,
    `L ${se},${bl}`,
    `L ${stEnd},${bl - stElev}`,
    `C ${stEnd + 5},${bl - stElev} ${tp - 2},${bl - stElev - 26} ${tp},${bl - stElev - 26}`,
    `C ${tp + 2},${bl - stElev - 26} ${te - 4},${bl} ${te},${bl}`,
  ].join(" ");
}

function wideQRS(x: number, bl = BL, amplitude = 80, width = 52): string {
  const half = Math.floor(width / 2);
  return [
    `L ${x},${bl}`,
    `L ${x + 5},${bl + 10}`,
    `C ${x + 10},${bl + 10} ${x + half - 4},${bl - amplitude} ${x + half},${bl - amplitude}`,
    `C ${x + half + 4},${bl - amplitude} ${x + width - 6},${bl + 12} ${x + width},${bl + 12}`,
    `L ${x + width + 7},${bl}`,
  ].join(" ");
}

function pWave(x: number, bl = BL): string {
  return [
    `L ${x},${bl}`,
    `C ${x + 4},${bl} ${x + 7},${bl - 14} ${x + 10},${bl - 14}`,
    `C ${x + 13},${bl - 14} ${x + 16},${bl} ${x + 20},${bl}`,
  ].join(" ");
}

function sawtooth(x: number, count: number, bl = BL): string {
  let d = `L ${x},${bl}`;
  for (let i = 0; i < count; i++) {
    const ox = x + i * 28;
    d += ` L ${ox + 20},${bl - 22} L ${ox + 28},${bl}`;
  }
  return d;
}

function fibBaseline(x: number, length: number, bl = BL): string {
  const seed = [0, 5, -3, 7, -6, 2, -8, 4, -2, 6, -5, 1, -7, 3, 0, 5, -4, 8, -2, 5];
  let d = "";
  const step = length / seed.length;
  for (let i = 0; i < seed.length; i++) {
    const cx = x + i * step;
    const cy = bl + seed[i];
    const nx = x + (i + 1) * step;
    const ny = bl + (seed[(i + 1) % seed.length] * 0.5);
    d += ` C ${cx + step * 0.3},${cy} ${nx - step * 0.3},${ny} ${nx},${ny}`;
  }
  return d;
}

function vfibPath(): string {
  const noiseSeq = [18,-32,52,-16,38,-58,24,-40,28,-48,36,-20,46,-62,16,
    -28,44,-52,20,-36,48,-26,32,-46,18,-30,40,-56,26,-38,
    50,-16,36,-60,22,-42,44,-18,32,-54,24,-34,46,-62,20,-40,
    38,-22,48,-50,28,-36,42,-58,18,-26,44,-46,30,-64,26,-32,
    52,-20,34,-56,40,-14,28,-48,42,-32,50,-58,16,-38,36,-52];
  let d = `M 0,${BL}`;
  let xi = 8;
  for (let i = 0; i < noiseSeq.length; i++) {
    const step = 3 + Math.abs(noiseSeq[i]) / 10;
    xi += step;
    if (xi > W - 8) break;
    d += ` L ${xi},${BL + noiseSeq[i]}`;
  }
  d += ` L ${W},${BL}`;
  return d;
}

function calPulse(): string {
  return `M 8,${BL} L 8,${BL} L 8,${BL - 50} L 28,${BL - 50} L 28,${BL}`;
}

const RHYTHM_CONFIGS: Record<string, {
  label: string;
  rate: string;
  features: string[];
  buildPath: () => string;
}> = {
  normal: {
    label: "Normal Sinus Rhythm",
    rate: "60–100 bpm",
    features: ["Regular P waves before each QRS", "Normal PR interval (0.12–0.20s)", "Narrow QRS < 0.12s"],
    buildPath: () => {
      const RR = 166;
      let d = `M 0,${BL} L 50,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(50 + i * RR);
        if (i < 4) d += ` L ${50 + i * RR + 130},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  bradycardia: {
    label: "Sinus Bradycardia",
    rate: "< 60 bpm",
    features: ["Regular P waves before each QRS", "Prolonged RR interval", "Normal P-QRS-T morphology"],
    buildPath: () => {
      const RR = 300;
      let d = `M 0,${BL} L 30,${BL}`;
      for (let i = 0; i < 3; i++) {
        d += nsr(30 + i * RR);
        if (i < 2) d += ` L ${30 + i * RR + 130},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  tachycardia: {
    label: "Sinus Tachycardia",
    rate: "> 100 bpm",
    features: ["P wave before each QRS (may merge with T)", "Short RR interval", "Normal QRS morphology"],
    buildPath: () => {
      const RR = 100;
      let d = `M 0,${BL} L 20,${BL}`;
      for (let i = 0; i < 9; i++) {
        d += nsr(20 + i * RR, BL, 82, 26);
        if (i < 8) d += ` L ${20 + i * RR + 112},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  afib: {
    label: "Atrial Fibrillation",
    rate: "100–175 bpm (irregular)",
    features: ["No distinct P waves — fibrillatory baseline", "Irregularly irregular RR intervals", "Narrow QRS (unless aberrant)"],
    buildPath: () => {
      const qrsOffsets = [55, 165, 305, 400, 530, 650, 760, 875];
      let d = `M 0,${BL}`;
      d += fibBaseline(0, qrsOffsets[0], BL);
      for (let i = 0; i < qrsOffsets.length; i++) {
        const xq = qrsOffsets[i];
        const nextX = qrsOffsets[i + 1] ?? W;
        const se = xq + 9;
        d += ` L ${xq},${BL} L ${xq + 2},${BL + 6} L ${xq + 5},${BL - 78} L ${xq + 8},${BL + 9} L ${se},${BL}`;
        d += fibBaseline(se, nextX - se, BL);
      }
      return d;
    },
  },
  flutter: {
    label: "Atrial Flutter",
    rate: "Atrial 300 bpm  |  Ventricular 150 bpm",
    features: ["Regular sawtooth P waves at 300 bpm", "2:1 AV block — QRS every other flutter wave", "Narrow QRS complexes"],
    buildPath: () => {
      let d = `M 0,${BL}`;
      d += sawtooth(0, 35, BL);
      const qrsAt = [56, 112, 168, 224, 280, 336, 392, 448, 504, 560, 616, 672, 728, 784, 840, 896, 952];
      for (let i = 0; i < qrsAt.length; i++) {
        if (i % 2 !== 0) continue;
        const xq = qrsAt[i];
        if (xq > W - 20) break;
        d += ` L ${xq},${BL} L ${xq + 2},${BL + 6} L ${xq + 5},${BL - 74} L ${xq + 8},${BL + 9} L ${xq + 14},${BL}`;
        d += sawtooth(xq + 14, 2, BL);
      }
      return d;
    },
  },
  svt: {
    label: "Supraventricular Tachycardia (SVT)",
    rate: "150–250 bpm",
    features: ["Very fast, regular rhythm", "Narrow QRS complexes", "P waves hidden in or after QRS"],
    buildPath: () => {
      const RR = 76;
      let d = `M 0,${BL} L 12,${BL}`;
      for (let i = 0; i < 12; i++) {
        const x = 12 + i * RR;
        const se = x + 13;
        d += ` L ${x},${BL} L ${x + 2},${BL + 6} L ${x + 5},${BL - 76} L ${x + 8},${BL + 9} L ${se},${BL}`;
        d += ` C ${se + 9},${BL} ${se + 18},${BL - 9} ${se + 26},${BL - 9} C ${se + 34},${BL - 9} ${se + 40},${BL} ${se + 42},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  pvcs: {
    label: "Premature Ventricular Contractions (PVCs)",
    rate: "60–80 bpm with ectopic beats",
    features: ["Wide, bizarre QRS — no preceding P wave", "Compensatory pause follows PVC", "Normal beats between PVCs"],
    buildPath: () => {
      const pvcAt = [2, 5];
      let d = `M 0,${BL} L 28,${BL}`;
      let x = 28;
      for (let i = 0; i < 6; i++) {
        if (pvcAt.includes(i)) {
          d += wideQRS(x, BL, 86, 52);
          x += 130;
        } else {
          d += nsr(x);
          x += 164;
        }
        d += ` L ${x},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  vtach: {
    label: "Ventricular Tachycardia (VTach)",
    rate: "100–250 bpm",
    features: ["Wide, bizarre QRS > 0.12s", "No discernible P waves", "Regular rapid rhythm"],
    buildPath: () => {
      const RR = 90;
      let d = `M 0,${BL} L 15,${BL}`;
      for (let i = 0; i < 10; i++) {
        const x = 15 + i * RR;
        d += wideQRS(x, BL, 82, 50);
        d += ` L ${x + 58},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  vfib: {
    label: "Ventricular Fibrillation (VFib)",
    rate: "No organized rate",
    features: ["Chaotic, irregular waveform", "No P waves or QRS complexes", "No cardiac output — EMERGENCY"],
    buildPath: vfibPath,
  },
  block1: {
    label: "First Degree AV Block",
    rate: "60–100 bpm",
    features: ["PR interval > 0.20s (prolonged)", "Every P wave followed by QRS", "Normal QRS morphology"],
    buildPath: () => {
      const RR = 172;
      let d = `M 0,${BL} L 28,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(28 + i * RR, BL, 90, 64);
        if (i < 4) d += ` L ${28 + i * RR + 140},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },
  block3: {
    label: "Third Degree (Complete) AV Block",
    rate: "Ventricular: 20–40 bpm  |  Atrial: 60–100 bpm",
    features: ["P waves & QRS completely independent", "Wide, slow ventricular escape rhythm", "No P-to-QRS association"],
    buildPath: () => {
      let d = `M 0,${BL} L 10,${BL}`;
      const pWaveOffsets = [10, 82, 154, 226, 298, 370, 442, 514, 586, 658, 730, 802, 874, 946];
      const qrsOffsets = [50, 290, 530, 770];
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
          cx = ev.x + 20;
        } else {
          d += wideQRS(ev.x, BL, 78, 52);
          cx = ev.x + 60;
        }
      }
      d += ` L ${W},${BL}`;
      return cx > 0 ? d : d;
    },
  },
  stemi: {
    label: "ST Elevation (STEMI)",
    rate: "60–100 bpm",
    features: ["ST segment elevated > 1mm above isoelectric", "Acute myocardial infarction pattern", "ST elevation in contiguous leads"],
    buildPath: () => {
      const RR = 166;
      let d = `M 0,${BL} L 50,${BL}`;
      for (let i = 0; i < 5; i++) {
        d += nsr(50 + i * RR, BL, 92, 34, 24);
        if (i < 4) d += ` L ${50 + i * RR + 130},${BL}`;
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
  const uid = `ekg-${key}`;

  return (
    <div className="mb-8">
      <div className="rounded-xl overflow-hidden border-2 border-[#c8a090] shadow-md">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full block"
          style={{ background: "#fff3ee" }}
          aria-label={`ECG strip showing ${config.label}`}
        >
          <defs>
            <pattern id={`sg-${uid}`} width={SMALL} height={SMALL} patternUnits="userSpaceOnUse">
              <path d={`M ${SMALL} 0 L 0 0 0 ${SMALL}`} fill="none" stroke="rgba(210,100,80,0.22)" strokeWidth="0.4" />
            </pattern>
            <pattern id={`lg-${uid}`} width={LARGE} height={LARGE} patternUnits="userSpaceOnUse">
              <rect width={LARGE} height={LARGE} fill={`url(#sg-${uid})`} />
              <path d={`M ${LARGE} 0 L 0 0 0 ${LARGE}`} fill="none" stroke="rgba(200,80,60,0.50)" strokeWidth="0.9" />
            </pattern>
          </defs>

          <rect width={W} height={H} fill={`url(#lg-${uid})`} />

          <line x1="0" y1={BL} x2={W} y2={BL} stroke="rgba(200,80,60,0.35)" strokeWidth="0.6" />

          <path d={calPulse()} fill="none" stroke="#111" strokeWidth="1.6" strokeLinecap="square" strokeLinejoin="miter" />

          <path
            d={path}
            fill="none"
            stroke="#111"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          <text x={W - 8} y={H - 8} textAnchor="end" fontSize="11" fill="rgba(120,60,50,0.6)" fontFamily="monospace" fontWeight="500">
            Lead II  25mm/s  10mm/mV
          </text>
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-bold px-2.5 py-1 rounded-full bg-red-50 text-red-700 border border-red-200">
          {config.label}
        </span>
        <span className="text-xs px-2 py-1 rounded-full bg-slate-100 text-slate-600 font-medium">
          {config.rate}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {config.features.map((f) => (
          <span
            key={f}
            className="inline-flex items-center text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100"
          >
            {f}
          </span>
        ))}
      </div>
    </div>
  );
}
