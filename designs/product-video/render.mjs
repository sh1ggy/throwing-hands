// Renders video.html to an MP4, frame by frame, with system Chrome and ffmpeg.
//   node render.mjs                 -> throwing-hands-product.mp4 (silent, 1920x1080, 30 fps)
//   node render.mjs --cut          -> throwing-hands-30s.mp4 (the 30 s cut)
//   node render.mjs --stills 5,20   -> stills/t5.png, stills/t20.png for review
// Needs puppeteer-core (npm i puppeteer-core) and ffmpeg with libx264 on PATH.
import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const fps = 30;
const stillsArg = process.argv.indexOf('--stills');
const cut = process.argv.includes('--cut');

const browser = await puppeteer.launch({ executablePath: chrome, headless: true, args: ['--force-device-scale-factor=1'] });
const page = await browser.newPage();
await page.setViewport({ width: 1920, height: 1080 });
await page.goto('file://' + path.join(here, 'video.html') + '?render=1' + (cut ? '&cut=30' : ''));
const dur = await page.evaluate(() => window.DURATION);

if (stillsArg > 0) {
  mkdirSync(path.join(here, 'stills'), { recursive: true });
  for (const t of process.argv[stillsArg + 1].split(',').map(Number)) {
    await page.evaluate(t => window.renderAt(t), t);
    await page.screenshot({ path: path.join(here, 'stills', `${cut ? 'cut' : 't'}${t}.png`) });
  }
} else {
  const out = path.join(here, cut ? 'throwing-hands-30s.mp4' : 'throwing-hands-product.mp4');
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-i', '-',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const n = Math.ceil(dur * fps);
  for (let f = 0; f < n; f++) {
    await page.evaluate(t => window.renderAt(t), f / fps);
    const buf = await page.screenshot({ type: 'jpeg', quality: 93 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (f % 300 === 0) console.log(`frame ${f}/${n}`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  console.log('wrote', out, `(${dur.toFixed(1)} s)`);
}
await browser.close();
