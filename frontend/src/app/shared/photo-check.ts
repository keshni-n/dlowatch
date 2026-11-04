/**
 * Checks a report photo on the phone, before it is sent. It only gives a hint: the person can always send anyway,
 * and the server never trusts it (anyone can call the API without this page).
 *
 * 1. Simple checks, no model: too dark, nearly blank (lens covered, plain wall), too blurry.
 * 2. Optional model: plug a trained classifier into PHOTO_MODEL (for example a small MobileNet trained on
 *    "water or leak", "pipe", "not relevant" photos). Until then it is not used.
 */

export type PhotoIssue = 'dark' | 'blank' | 'blurry' | 'not_relevant';

export interface PhotoCheck {
  ok: boolean;                 // nothing wrong found
  issues: PhotoIssue[];
  label: string | null;        // from the model, when there is one: "water", "pipe", "other"
  confidence: number | null;   // 0 to 1
  model: string | null;        // which model gave the label (null = simple checks only)
  brightness: number;          // 0 to 255
  sharpness: number;           // 0 to 100, from the blur effect (100 = sharp)
  kept_anyway?: boolean;       // the person was warned and sent it anyway
}

/** A trained model, once there is one. Return null if it cannot decide. */
export interface PhotoModel {
  name: string;
  classify(img: ImageData): Promise<{ label: string; confidence: number; relevant: boolean } | null>;
}

export const PHOTO_MODEL: { current: PhotoModel | null } = { current: null };

// Limits, set on purpose on the lenient side: a dark, slightly blurry photo of a real burst at night must pass.
const DARK_BELOW = 30;          // mean brightness: nearly black
const DIM_P95_BELOW = 70;       // even the brightest 5% of the photo is dim: too dark to see anything
const BLANK_SPREAD_BELOW = 8;   // brightness spread (standard deviation)
const BLURRY_ABOVE = 0.55;      // blur effect (Crete et al. 2007), 0 = sharp, 1 = very blurry
const WIDTH = 512;              // the photo is checked at this width
const MODEL_MIN_CONFIDENCE = 0.6;

/** Kinds where a photo of water or a pipe is expected. For "no water at home" any photo is fine. */
export const PHOTO_KINDS = ['water_on_road', 'broken_pipe'];

export async function checkPhoto(dataUrl: string, kind?: string): Promise<PhotoCheck> {
  const img = await load(dataUrl);
  const w = WIDTH;
  const h = Math.max(1, Math.round((img.height * w) / img.width));
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, w, h);
  const data = ctx.getImageData(0, 0, w, h);

  // grey levels
  const g = new Float32Array(w * h);
  let sum = 0;
  for (let i = 0, p = 0; i < g.length; i++, p += 4) {
    g[i] = 0.299 * data.data[p] + 0.587 * data.data[p + 1] + 0.114 * data.data[p + 2];
    sum += g[i];
  }
  const mean = sum / g.length;
  let sq = 0;
  for (const v of g) sq += (v - mean) ** 2;
  const spread = Math.sqrt(sq / g.length);

  // brightness of the brightest 5%: a photo where nothing is bright shows nothing
  const hist = new Uint32Array(256);
  for (const v of g) hist[Math.min(255, v | 0)]++;
  let acc = 0, p95 = 255;
  for (let b = 0; b < 256; b++) { acc += hist[b]; if (acc >= 0.95 * g.length) { p95 = b; break; } }

  // blur: how much detail a deliberate extra blur removes. A sharp photo loses a lot, a blurry one
  // has little left to lose. Same as skimage.measure.blur_effect (Crete et al. 2007).
  const blur = blurEffect(g, w, h, 11);
  const sharp = Math.round((1 - blur) * 100);

  const issues: PhotoIssue[] = [];
  if (mean < DARK_BELOW || p95 < DIM_P95_BELOW) issues.push('dark');
  else if (spread < BLANK_SPREAD_BELOW) issues.push('blank');
  else if (blur > BLURRY_ABOVE) issues.push('blurry');

  let label: string | null = null, confidence: number | null = null, model: string | null = null;
  const m = PHOTO_MODEL.current;
  if (m && !issues.length) {
    try {
      const r = await m.classify(data);
      if (r) {
        label = r.label;
        confidence = Math.round(r.confidence * 100) / 100;
        model = m.name;
        if (!r.relevant && r.confidence >= MODEL_MIN_CONFIDENCE && (!kind || PHOTO_KINDS.includes(kind))) {
          issues.push('not_relevant');
        }
      }
    } catch { /* a broken model must never stop a report */ }
  }
  return { ok: !issues.length, issues, label, confidence, model, brightness: Math.round(mean), sharpness: sharp };
}

/** Blur effect, 0 (sharp) to 1 (blurry): the larger of the horizontal and vertical values. */
export function blurEffect(g: Float32Array, w: number, h: number, size = 11): number {
  let worst = 0;
  for (const axis of [0, 1]) {
    const f = boxBlur(g, w, h, size, axis);
    let m1 = 0, m2 = 0;
    for (let y = 2; y < h - 1; y++) {
      for (let x = 2; x < w - 1; x++) {
        const a = Math.abs(sobel(g, w, h, x, y, axis)), b = Math.abs(sobel(f, w, h, x, y, axis));
        m1 += a;
        m2 += Math.max(0, a - b);
      }
    }
    if (m1 > 0) worst = Math.max(worst, Math.abs(m1 - m2) / m1);
  }
  return worst;
}

/** Sobel along one axis (0 = down the rows, 1 = across the columns), mirror at the edges like scipy. */
function sobel(g: Float32Array, w: number, h: number, x: number, y: number, axis: number): number {
  const at = (xx: number, yy: number) => g[mirror(yy, h) * w + mirror(xx, w)];
  if (axis === 0) {
    return (at(x - 1, y + 1) + 2 * at(x, y + 1) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x, y - 1) + at(x + 1, y - 1));
  }
  return (at(x + 1, y - 1) + 2 * at(x + 1, y) + at(x + 1, y + 1)) - (at(x - 1, y - 1) + 2 * at(x - 1, y) + at(x - 1, y + 1));
}

/** Moving average of `size` pixels along one axis, mirror at the edges like scipy's uniform_filter1d. */
function boxBlur(g: Float32Array, w: number, h: number, size: number, axis: number): Float32Array {
  const out = new Float32Array(g.length);
  const half = size >> 1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let s = 0;
      for (let k = -half; k < size - half; k++) {
        s += axis === 0 ? g[mirror(y + k, h) * w + x] : g[y * w + mirror(x + k, w)];
      }
      out[y * w + x] = s / size;
    }
  }
  return out;
}

function mirror(i: number, n: number): number {
  if (i < 0) return -i - 1;
  if (i >= n) return 2 * n - i - 1;
  return i;
}

function load(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not read the photo'));
    img.src = src;
  });
}
