// Puppeteer standard : bundled Chromium par défaut (setup local), ou Chromium
// système si PUPPETEER_EXECUTABLE_PATH est set (Docker).
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const CHROMIUM_EXECUTABLE = process.env.PUPPETEER_EXECUTABLE_PATH || undefined;

// Format LinkedIn 2026 best practice: portrait 4:5 (1080x1350)
const SLIDE_WIDTH = 1080;
const SLIDE_HEIGHT = 1350;

async function generateCarouselPdf(slidesInputPath, outputPath) {
  const slidesContent = fs.readFileSync(slidesInputPath, 'utf8');
  const slides = JSON.parse(slidesContent);
  if (!Array.isArray(slides) || slides.length === 0) {
    throw new Error(`Invalid slides input from ${slidesInputPath}`);
  }
  const templatePath = path.join(__dirname, 'templates', 'carousel.html');
  const template = fs.readFileSync(templatePath, 'utf8');

  let slidesHtml = '';
  slides.forEach((text, i) => {
    const isFirst = i === 0;
    const isLast = i === slides.length - 1;
    const slideClass = isFirst ? 'slide slide-1' : isLast ? 'slide slide-cta' : 'slide slide-body';

    const lines = text.trim().split('\n').filter(Boolean);
    const main = lines[0] || '';
    const sub = lines.slice(1).join(' ');

    const saveCue = isLast
      ? '<div class="save-cue">💾 Garde ce post si tu veux y revenir</div>'
      : '';

    slidesHtml += `
    <div class="${slideClass}">
      <span class="slide-number">${i + 1} / ${slides.length}</span>
      <div class="slide-content">
        <div class="accent-line"></div>
        <div class="main-text">${escapeHtml(main)}</div>
        ${sub ? `<div class="sub-text">${escapeHtml(sub)}</div>` : ''}
      </div>
      ${saveCue}
      <div class="branding">Victor Lenain · Développeur full-stack · Intégration IA</div>
    </div>`;
  });

  const html = template.replace('{{SLIDES_HTML}}', slidesHtml);
  const tmpHtml = outputPath.replace('.pdf', '.tmp.html');
  fs.writeFileSync(tmpHtml, html, 'utf8');

  let browser;
  try {
    const launchOptions = {
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    };
    if (CHROMIUM_EXECUTABLE) {
      launchOptions.executablePath = CHROMIUM_EXECUTABLE;
    }
    browser = await puppeteer.launch(launchOptions);
    const page = await browser.newPage();
    await page.setViewport({ width: SLIDE_WIDTH, height: SLIDE_HEIGHT });
    await page.goto(`file://${tmpHtml}`, { waitUntil: 'networkidle0' });
    await page.pdf({
      path: outputPath,
      width: `${SLIDE_WIDTH}px`,
      height: `${SLIDE_HEIGHT}px`,
      printBackground: true,
    });
    console.log(`PDF generated: ${outputPath}`);
  } finally {
    if (browser) {
      try { await browser.close(); } catch (e) { /* ignore */ }
    }
    try { fs.unlinkSync(tmpHtml); } catch (e) { /* tmp may not exist */ }
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const slidesPathArg = process.argv[2];
const outputArg = process.argv[3];
if (!slidesPathArg || !outputArg) {
  console.error('Usage: node html_to_pdf.js <slides.json path> <output.pdf>');
  process.exit(1);
}
generateCarouselPdf(slidesPathArg, outputArg).catch(e => { console.error(e); process.exit(1); });
