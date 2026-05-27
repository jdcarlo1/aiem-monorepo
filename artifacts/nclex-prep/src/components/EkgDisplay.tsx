/**
 * EkgDisplay — Real EKG paper simulation
 *
 * Scale: 1mm = 4px  |  25 mm/s paper speed  |  10 mm/mV gain
 * → 10-second strip = 250mm = 1000px wide
 * → Standard small square = 1mm = 4px
 * → Standard large square = 5mm = 20px
 * → Normal RR at 75 bpm = 0.8s = 20mm = 80px
 * → QRS width (0.10s) = 2.5mm = 10px  ← very narrow/sharp
 * → R wave amplitude (1mV) = 10mm = 40px above baseline
 */

const W = 1000;
const H = 300;
const BL = 195;   // baseline y — leaves ~95px above for R waves, ~60px below for S/noise
const MM = 4;     // pixels per mm
const SQ = MM;    // small grid square: 1mm = 4px
const LG = MM * 5; // large grid square: 5mm = 20px

// ─── Waveform helper functions ────────────────────────────────────────────────

/** Standard PQRST complex — all dimensions in mm for easy reading */
function beat(x: number, opts: {
  pAmp?: number;    // P amplitude (mm above BL), default 2.5
  pDur?: number;    // P wave duration (mm), default 10
  pr?: number;      // PR interval from P-start to QRS-start (mm), default 16
  qAmp?: number;    // Q dip (mm below BL), default 0.8
  rAmp?: number;    // R height (mm above BL), default 13
  sAmp?: number;    // S dip (mm below BL), default 2.5
  stLen?: number;   // ST segment length (mm), default 5
  stElev?: number;  // ST elevation (mm), default 0 (+ = elevated, - = depressed)
  tAmp?: number;    // T amplitude (mm above BL), default 5.5
  tDur?: number;    // T wave duration (mm), default 20
  bl?: number;
} = {}): string {
  const {
    pAmp = 2.5, pDur = 10, pr = 16,
    qAmp = 0.8, rAmp = 13, sAmp = 2.5,
    stLen = 5, stElev = 0,
    tAmp = 5.5, tDur = 20,
    bl = BL,
  } = opts;

  const p = pAmp * MM;
  const pd = pDur * MM;
  const prMM = pr * MM;
  const q = qAmp * MM;
  const r = rAmp * MM;
  const s = sAmp * MM;
  const stl = stLen * MM;
  const ste = stElev * MM;
  const t = tAmp * MM;
  const td = tDur * MM;

  // Key x-coordinates
  const pStart = x;
  const pMid   = x + pd * 0.5;
  const pEnd   = x + pd;
  const qrs0   = x + prMM;          // QRS start
  const qTip   = qrs0 + MM * 0.8;   // Q trough — very brief
  const rPeak  = qrs0 + MM * 2;     // R peak — 2mm after QRS start
  const sTip   = qrs0 + MM * 3;     // S trough — 1mm after R peak
  const stS    = qrs0 + MM * 4;     // ST segment starts — back near BL
  const stE    = stS + stl;         // ST segment ends
  const tMid   = stE + td * 0.5;
  const tEnd   = stE + td;

  return [
    `L ${pStart},${bl}`,
    // P wave — smooth rounded bump
    `C ${pStart + pd*0.12},${bl} ${pMid - pd*0.22},${bl - p} ${pMid},${bl - p}`,
    `C ${pMid + pd*0.22},${bl - p} ${pEnd - pd*0.12},${bl} ${pEnd},${bl}`,
    // PR isoelectric segment
    `L ${qrs0},${bl}`,
    // Q — tiny sharp dip
    `L ${qTip},${bl + q}`,
    // R — sharp narrow spike (straight lines = authentic EKG look)
    `L ${rPeak},${bl - r}`,
    // S — sharp dip below BL
    `L ${sTip},${bl + s}`,
    // return to isoelectric + ST segment
    `L ${stS},${bl - ste}`,
    `L ${stE},${bl - ste}`,
    // T wave — smooth asymmetric bump (slower rise, faster fall like real ECG)
    `C ${stE + td*0.15},${bl - ste} ${tMid - td*0.18},${bl - ste - t} ${tMid},${bl - ste - t}`,
    `C ${tMid + td*0.18},${bl - ste - t} ${tEnd - td*0.12},${bl} ${tEnd},${bl}`,
  ].join(" ");
}

/** Narrow QRS-only (no P, no T) for SVT / fast rhythms */
function qrsOnly(x: number, opts: { rAmp?: number; sAmp?: number; bl?: number } = {}): string {
  const { rAmp = 13, sAmp = 2.5, bl = BL } = opts;
  const r = rAmp * MM;
  const s = sAmp * MM;
  const qTip = x + MM * 0.6;
  const rPeak = x + MM * 2;
  const sTip  = x + MM * 3;
  const stS   = x + MM * 4;
  return [
    `L ${x},${bl}`,
    `L ${qTip},${bl + MM * 0.8}`,
    `L ${rPeak},${bl - r}`,
    `L ${sTip},${bl + s}`,
    `L ${stS},${bl}`,
  ].join(" ");
}

/** Wide bizarre QRS — ventricular origin (VTach, PVC) */
function wideQRS(x: number, opts: { rAmp?: number; width?: number; bl?: number } = {}): string {
  const { rAmp = 11, width = 14, bl = BL } = opts;
  const r = rAmp * MM;
  const w = width * MM;
  return [
    `L ${x},${bl}`,
    `L ${x + MM},${bl + MM * 2.5}`,       // initial slurred deflection
    `C ${x + w*0.25},${bl + MM*2} ${x + w*0.4},${bl - r} ${x + w*0.5},${bl - r}`,
    `C ${x + w*0.6},${bl - r} ${x + w*0.75},${bl + MM*2.5} ${x + w},${bl + MM*2.5}`,
    `L ${x + w + MM * 1.5},${bl}`,
  ].join(" ");
}

/** P wave only (standalone, for 3rd-degree block) */
function pWaveOnly(x: number, bl = BL): string {
  const p = 2.5 * MM;
  const pd = 10 * MM;
  const mid = x + pd * 0.5;
  return [
    `L ${x},${bl}`,
    `C ${x + pd*0.12},${bl} ${mid - pd*0.22},${bl - p} ${mid},${bl - p}`,
    `C ${mid + pd*0.22},${bl - p} ${x + pd - pd*0.12},${bl} ${x + pd},${bl}`,
  ].join(" ");
}

/** Sawtooth flutter waves (atrial flutter) */
function sawtoothWaves(xStart: number, xEnd: number, bl = BL): string {
  const flutterPeriod = MM * 6.5; // 300bpm = 0.2s = 5mm but we stretch slightly for visibility
  let d = `L ${xStart},${bl}`;
  let cx = xStart;
  while (cx + flutterPeriod < xEnd) {
    const top = cx + flutterPeriod * 0.65;
    d += ` L ${top},${bl - MM*6} L ${cx + flutterPeriod},${bl}`;
    cx += flutterPeriod;
  }
  return d;
}

/** Fibrillatory baseline (atrial fibrillation) */
function fibBaseline(xStart: number, xEnd: number, bl = BL): string {
  const amps = [2,-3,4,-2,5,-4,3,-5,2,-3,4,-1,5,-3,2,-4,3,-2,4,-3];
  const step = (xEnd - xStart) / amps.length;
  let d = "";
  for (let i = 0; i < amps.length; i++) {
    const cx1 = xStart + i * step;
    const cy1 = bl + amps[i] * MM * 0.45;
    const cx2 = xStart + (i + 1) * step;
    const cy2 = bl + amps[(i + 1) % amps.length] * MM * 0.35;
    d += ` C ${cx1 + step*0.25},${cy1} ${cx2 - step*0.25},${cy2} ${cx2},${cy2}`;
  }
  return d;
}

/** V-fib: chaotic irregular waveform */
function vfibPath(): string {
  const seq = [16,-28,44,-18,36,-52,22,-38,48,-24,30,-46,18,-32,42,-54,26,-36,50,-16,38,-58,20,-34,46,-26,32,-50,24,-42,40,-20,34,-56,28,-40,52,-18,36,-60,22,-44,48,-28,32,-52,18,-36,44,-22,38,-54,26,-46,50,-16,40,-58,24,-34];
  let d = `M 0,${BL}`;
  let xi = 6;
  for (let i = 0; i < seq.length; i++) {
    const step = 2.5 + Math.abs(seq[i]) / 10;
    xi += step;
    if (xi > W - 6) break;
    const prev = i > 0 ? seq[i-1] * MM * 0.4 : 0;
    const curr = seq[i] * MM * 0.4;
    d += ` C ${xi - step*0.5},${BL + prev} ${xi - step*0.3},${BL + curr} ${xi},${BL + curr}`;
  }
  d += ` L ${W},${BL}`;
  return d;
}

/** Calibration pulse: 1mV square wave (10mm tall, 5mm wide = 40px×20px) */
function calPulse(): string {
  const h = 10 * MM;
  const w = 5 * MM;
  const x0 = 6;
  return `M ${x0},${BL} L ${x0},${BL - h} L ${x0 + w},${BL - h} L ${x0 + w},${BL}`;
}

// ─── Rhythm configurations ────────────────────────────────────────────────────

const CONFIGS: Record<string, {
  label: string;
  rate: string;
  features: string[];
  buildPath: () => string;
}> = {
  normal: {
    label: "Normal Sinus Rhythm",
    rate: "60–100 bpm",
    features: ["Regular P waves before each QRS", "PR interval 0.12–0.20s", "Narrow QRS < 0.12s", "Rate 60–100 bpm"],
    buildPath: () => {
      const RR = 80 * MM; // 80mm = 0.8s → 75 bpm (6 full beats in ~960px before cal pulse offset)
      let d = `M 0,${BL} L ${8*MM},${BL}`;
      const start = 8 * MM;
      for (let i = 0; i < 6; i++) {
        d += beat(start + i * RR);
        d += ` L ${start + i * RR + 44 * MM},${BL}`;
      }
      return d;
    },
  },

  bradycardia: {
    label: "Sinus Bradycardia",
    rate: "< 60 bpm",
    features: ["Slow but regular rhythm", "Normal P-QRS-T morphology", "RR interval > 1.0 second", "Rate < 60 bpm"],
    buildPath: () => {
      const RR = 130 * MM; // ~46bpm visible — spread 3 full beats
      let d = `M 0,${BL} L ${8*MM},${BL}`;
      const starts = [8*MM, 8*MM + RR, 8*MM + 2*RR];
      for (const x of starts) {
        if (x > W - 30) break;
        d += beat(x);
        d += ` L ${x + 44*MM},${BL}`;
      }
      return d;
    },
  },

  tachycardia: {
    label: "Sinus Tachycardia",
    rate: "> 100 bpm",
    features: ["Fast but regular rhythm", "P wave before each QRS (may overlap T)", "Short RR interval", "Rate > 100 bpm"],
    buildPath: () => {
      const RR = 37 * MM; // ~162mm RR = 0.49s → ~122bpm
      let d = `M 0,${BL} L ${4*MM},${BL}`;
      const start = 4 * MM;
      for (let i = 0; i < 12; i++) {
        const x = start + i * RR;
        if (x + 30*MM > W) break;
        d += beat(x, { pAmp: 2, pDur: 8, pr: 14, rAmp: 11, tAmp: 4.5, tDur: 14 });
        d += ` L ${x + 28*MM},${BL}`;
      }
      return d;
    },
  },

  afib: {
    label: "Atrial Fibrillation",
    rate: "Irregularly irregular, 100–175 bpm",
    features: ["No distinct P waves", "Chaotic fibrillatory baseline", "Irregularly irregular RR intervals", "Narrow QRS"],
    buildPath: () => {
      const qrsPositions = [50, 148, 258, 336, 476, 592, 688, 800, 908];
      let d = `M 0,${BL}`;
      let prevEnd = 0;
      for (let i = 0; i < qrsPositions.length; i++) {
        const xq = qrsPositions[i];
        d += fibBaseline(prevEnd, xq);
        d += qrsOnly(xq, { rAmp: 11, sAmp: 2 });
        // short ST + tiny T
        d += ` C ${xq + 22},${BL} ${xq + 34},${BL - 14} ${xq + 38},${BL - 14} C ${xq + 42},${BL - 14} ${xq + 50},${BL} ${xq + 54},${BL}`;
        prevEnd = xq + 54;
      }
      d += fibBaseline(prevEnd, W);
      return d;
    },
  },

  flutter: {
    label: "Atrial Flutter",
    rate: "Atrial 300 bpm  |  Ventricular ~150 bpm",
    features: ["Regular sawtooth flutter waves at 300 bpm", "2:1 AV conduction ratio", "Narrow QRS complexes", "No isoelectric baseline"],
    buildPath: () => {
      const flutterW = 6.5 * MM;  // each flutter wave
      const qrsEvery2 = flutterW * 2; // QRS every 2nd flutter wave
      let d = `M 0,${BL}`;
      let cx = 10;
      let qrsCount = 0;
      while (cx < W - 10) {
        const top = cx + flutterW * 0.65;
        d += ` L ${top},${BL - MM*5.5} L ${cx + flutterW},${BL}`;
        qrsCount++;
        if (qrsCount % 2 === 0) {
          d += qrsOnly(cx + flutterW, { rAmp: 10, sAmp: 2 });
          d += ` L ${cx + flutterW + 4*MM},${BL}`;
          cx += flutterW + 4*MM;
        } else {
          cx += flutterW;
        }
      }
      return d;
    },
  },

  svt: {
    label: "Supraventricular Tachycardia (SVT)",
    rate: "150–250 bpm",
    features: ["Very fast regular rhythm", "Narrow QRS complexes", "P waves hidden in or buried after QRS", "Abrupt onset/termination"],
    buildPath: () => {
      const RR = 19 * MM; // very short cycle ~200bpm
      let d = `M 0,${BL} L ${4*MM},${BL}`;
      const start = 4 * MM;
      for (let i = 0; i < 14; i++) {
        const x = start + i * RR;
        if (x + 20*MM > W) break;
        d += qrsOnly(x, { rAmp: 10 });
        // retrograde P in ST
        const rp = x + 5*MM;
        d += ` C ${rp},${BL} ${rp + 4*MM},${BL + 6} ${rp + 6*MM},${BL + 6} C ${rp + 8*MM},${BL + 6} ${rp + 10*MM},${BL} ${rp + 11*MM},${BL}`;
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },

  pvcs: {
    label: "Premature Ventricular Contractions (PVCs)",
    rate: "60–80 bpm with ectopic beats",
    features: ["Wide bizarre QRS — no preceding P wave", "Full compensatory pause after PVC", "Normal beats between PVCs", "Bigeminy pattern shown"],
    buildPath: () => {
      let d = `M 0,${BL} L ${6*MM},${BL}`;
      let x = 6 * MM;
      const pattern = ["normal","pvc","normal","pvc","normal","pvc","normal"];
      for (const type of pattern) {
        if (x > W - 20) break;
        if (type === "normal") {
          d += beat(x);
          x += 80 * MM;
        } else {
          // PVC — wide, no preceding P
          d += wideQRS(x, { rAmp: 10, width: 16 });
          // compensatory pause then return
          x += 56 * MM;
        }
        d += ` L ${x},${BL}`;
      }
      return d;
    },
  },

  vtach: {
    label: "Ventricular Tachycardia (VTach)",
    rate: "100–250 bpm",
    features: ["Wide bizarre QRS > 0.12s", "No discernible P waves", "Regular rapid rhythm", "AV dissociation"],
    buildPath: () => {
      const RR = 22 * MM; // ~136 bpm
      let d = `M 0,${BL} L ${4*MM},${BL}`;
      const start = 4 * MM;
      for (let i = 0; i < 13; i++) {
        const x = start + i * RR;
        if (x + 20*MM > W) break;
        d += wideQRS(x, { rAmp: 10, width: 15 });
        d += ` L ${x + 20*MM},${BL}`;
      }
      return d;
    },
  },

  vfib: {
    label: "Ventricular Fibrillation (VFib)",
    rate: "No organized rate — CARDIAC ARREST",
    features: ["Chaotic irregular waveform", "No identifiable P, QRS, or T", "Absent cardiac output", "Immediate defibrillation required"],
    buildPath: vfibPath,
  },

  block1: {
    label: "First Degree AV Block",
    rate: "60–100 bpm",
    features: ["PR interval prolonged > 0.20s (> 5 small squares)", "Every P wave followed by QRS", "Normal QRS morphology", "Regular rhythm"],
    buildPath: () => {
      const RR = 85 * MM;
      let d = `M 0,${BL} L ${6*MM},${BL}`;
      const start = 6 * MM;
      for (let i = 0; i < 5; i++) {
        const x = start + i * RR;
        if (x + 50*MM > W) break;
        d += beat(x, { pr: 28 }); // prolonged PR (28mm = 0.28s)
        d += ` L ${x + 50*MM},${BL}`;
      }
      return d;
    },
  },

  block3: {
    label: "Third Degree (Complete) AV Block",
    rate: "Atrial 60–80 bpm  |  Ventricular 20–40 bpm",
    features: ["P waves and QRS are completely independent", "Wide slow ventricular escape rhythm", "No consistent P-to-QRS relationship", "P waves march through QRS undisturbed"],
    buildPath: () => {
      const atrialRR = 68 * MM;   // P-wave interval
      const ventriRR = 200 * MM;  // very slow ventricular escape
      const events: Array<{ x: number; type: "p" | "q" }> = [];
      for (let i = 0; i * atrialRR < W - 10; i++) {
        events.push({ x: 10 + i * atrialRR, type: "p" });
      }
      for (let i = 0; i * ventriRR < W - 10; i++) {
        events.push({ x: 40 + i * ventriRR, type: "q" });
      }
      events.sort((a, b) => a.x - b.x);

      let d = `M 0,${BL} L 10,${BL}`;
      for (const ev of events) {
        if (ev.x > W - 10) break;
        d += ` L ${ev.x},${BL}`;
        if (ev.type === "p") {
          d += pWaveOnly(ev.x);
        } else {
          d += wideQRS(ev.x, { rAmp: 9, width: 16 });
        }
      }
      d += ` L ${W},${BL}`;
      return d;
    },
  },

  stemi: {
    label: "ST Elevation (STEMI)",
    rate: "60–100 bpm",
    features: ["ST segment elevated > 1mm above isoelectric", "Acute myocardial infarction pattern", "Hyperacute T waves", "Contiguous lead involvement"],
    buildPath: () => {
      const RR = 80 * MM;
      let d = `M 0,${BL} L ${8*MM},${BL}`;
      const start = 8 * MM;
      for (let i = 0; i < 6; i++) {
        const x = start + i * RR;
        if (x + 50*MM > W) break;
        d += beat(x, { stElev: 4.5, stLen: 6, tAmp: 8, tDur: 22 }); // elevated ST, tall T
        d += ` L ${x + 50*MM},${BL}`;
      }
      return d;
    },
  },
};

// ─── Aliases ──────────────────────────────────────────────────────────────────

const ALIASES: Record<string, string> = {
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

// ─── Component ────────────────────────────────────────────────────────────────

export default function EkgDisplay({ rhythm }: { rhythm: string }) {
  const key = ALIASES[rhythm] ?? rhythm;
  const config = CONFIGS[key];

  if (!config) {
    return (
      <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4 text-yellow-700 text-sm mb-6">
        EKG strip unavailable for: <code>{rhythm}</code>
      </div>
    );
  }

  const waveformPath = config.buildPath();
  const uid = `ekg-${key}`;

  return (
    <div className="mb-6">
      {/* ── Strip container ── */}
      <div className="rounded-lg overflow-hidden border border-[#c8a090] shadow-lg" style={{ background: "#fff0ea" }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full block"
          aria-label={`ECG rhythm strip: ${config.label}`}
          style={{ display: "block" }}
        >
          <defs>
            {/* 1mm small grid square */}
            <pattern id={`sg-${uid}`} x="0" y="0" width={SQ} height={SQ} patternUnits="userSpaceOnUse">
              <path
                d={`M ${SQ} 0 L 0 0 0 ${SQ}`}
                fill="none"
                stroke="rgba(195,90,70,0.18)"
                strokeWidth="0.35"
              />
            </pattern>
            {/* 5mm large grid square */}
            <pattern id={`lg-${uid}`} x="0" y="0" width={LG} height={LG} patternUnits="userSpaceOnUse">
              <rect width={LG} height={LG} fill={`url(#sg-${uid})`} />
              <path
                d={`M ${LG} 0 L 0 0 0 ${LG}`}
                fill="none"
                stroke="rgba(185,70,55,0.42)"
                strokeWidth="0.75"
              />
            </pattern>
          </defs>

          {/* Paper background */}
          <rect width={W} height={H} fill="#fff0ea" />
          {/* Grid */}
          <rect width={W} height={H} fill={`url(#lg-${uid})`} />

          {/* Isoelectric baseline (subtle) */}
          <line x1="0" y1={BL} x2={W} y2={BL}
            stroke="rgba(180,65,50,0.25)" strokeWidth="0.5" />

          {/* Calibration pulse — 1mV square wave (10mm high × 5mm wide) */}
          <path
            d={calPulse()}
            fill="none"
            stroke="#1a0a08"
            strokeWidth="1.4"
            strokeLinecap="square"
            strokeLinejoin="miter"
          />

          {/* Main waveform */}
          <path
            d={waveformPath}
            fill="none"
            stroke="#1a0a08"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Labels */}
          <text x="8" y="14" fontSize="9.5" fill="rgba(130,55,40,0.65)"
            fontFamily="monospace" fontWeight="600" letterSpacing="0.3">
            Lead II
          </text>
          <text x={W - 8} y={H - 7} textAnchor="end" fontSize="9" fill="rgba(130,55,40,0.55)"
            fontFamily="monospace">
            25 mm/s  ·  10 mm/mV
          </text>
        </svg>
      </div>

      {/* ── Info badges ── */}
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-bold px-2.5 py-1 rounded-full bg-red-50 text-red-700 border border-red-200">
          {config.label}
        </span>
        <span className="text-xs px-2 py-1 rounded-full bg-slate-100 text-slate-600 font-medium">
          {config.rate}
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {config.features.map((f) => (
          <span key={f}
            className="inline-flex items-center text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100">
            {f}
          </span>
        ))}
      </div>
    </div>
  );
}
