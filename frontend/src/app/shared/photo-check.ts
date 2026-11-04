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
  sharpness: number;           // variance of the Laplacian on a 256 px wide copy
  kept_anyway?: boolean;       // the person was warned and sent it anyway
}

/** A trained model, once there is one. Return null if it cannot decide. */
export interface PhotoModel {
  name: string;
  classify(img: ImageData): Promise<{ label: string; confidence: number; relevant: boolean } | null>;
}

export const PHOTO_MODEL: { current: PhotoModel | null } = { current: null };

// Limits, set on purpose on the lenient side: a dark, slightly blurry photo of a real burst at night must pass.
const DARK_BELOW = 30;          // mean brightness
const BLANK_SPREAD_BELOW = 8;   // brightness spread (standard deviation)
const BLURRY_BELOW = 12;        // Laplacian variance
const MODEL_MIN_CONFIDENCE = 0.6;

/** Kinds where a photo of water or a pipe is expected. For "no water at home" any photo is fine. */
export const PHOTO_KINDS = ['water_on_road', 'broken_pipe'];

export async function checkPhoto(dataUrl: string, kind?: string): Promise<PhotoCheck> {
  const img = await load(dataUrl);
  const w = 256;
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

  // sharpness: variance of the Laplacian (edges). Blurry photos have few strong edges.
  let n = 0, ls = 0, ls2 = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const lap = g[i - 1] + g[i + 1] + g[i - w] + g[i + w] - 4 * g[i];
      ls += lap;
      ls2 += lap * lap;
      n++;
    }
  }
  const sharp = n ? ls2 / n - (ls / n) ** 2 : 0;

  const issues: PhotoIssue[] = [];
  if (mean < DARK_BELOW) issues.push('dark');
  else if (spread < BLANK_SPREAD_BELOW) issues.push('blank');
  else if (sharp < BLURRY_BELOW) issues.push('blurry');

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
  return { ok: !issues.length, issues, label, confidence, model, brightness: Math.round(mean), sharpness: Math.round(sharp) };
}

function load(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not read the photo'));
    img.src = src;
  });
}
