// LinkedIn carousel PDF generator.
// Input  : slides.json (list of strings, each "main\nsub")
// Output : PDF portrait 1080x1350 (best practice 2026)
//
// Toutes les slides partagent la même base visuelle (header logo + accent-bar
// + main-text + sub-text + footer). La cover se distingue par un titre plus
// gros (CSS .slide-cover) ; la dernière slide par la couleur CTA (.slide-cta).

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const SLIDE_WIDTH = 1080;
const SLIDE_HEIGHT = 1350;
const TEMPLATE_PATH = path.join(__dirname, 'templates', 'carousel.html');
const LOGO_PATH = path.join(__dirname, 'templates', 'logo-light.png');

const HTML_ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
};

function escapeHtml(str) {
  return String(str).replace(/[&<>"]/g, (c) => HTML_ESCAPES[c]);
}

function slideClass(i, total) {
  if (i === 0) return 'slide slide-cover';
  if (i === total - 1) return 'slide slide-cta';
  return 'slide slide-body';
}

function parseSlideText(text) {
  const lines = text.trim().split('\n').filter(Boolean);
  return {
    main: lines[0] || '',
    sub: lines.slice(1).join(' '),
  };
}

function fileUri(filePath) {
  return `file://${filePath.replace(/\\/g, '/')}`;
}

function renderSlide({ index, total, main, sub, logoUri }) {
  const isLast = index === total - 1;
  const logoTag = logoUri ? `<img src="${logoUri}" alt="Victor Lenain">` : '';
  const subBlock = sub ? `<div class="sub-text">${escapeHtml(sub)}</div>` : '';
  const saveCue = isLast
    ? '<div class="save-cue"><span class="save-cue-badge">ASTUCE</span> Enregistre ce post pour y revenir</div>'
    : '';
  const nextArrow = isLast ? '' : '<span class="next-arrow">&rarr;</span>';

  return `
    <div class="${slideClass(index, total)}">
      <div class="header">
        ${logoTag}
        <div class="header-text">
          <span class="brand">Victor Lenain</span>
          <span class="brand-sub">Dev freelance &middot; Int&eacute;gration IA</span>
        </div>
      </div>
      <span class="slide-number">${index + 1} / ${total}</span>
      <div class="body">
        <div class="accent-bar"></div>
        <div class="main-text">${escapeHtml(main)}</div>
        ${subBlock}
        ${saveCue}
      </div>
      <div class="footer">
        <span>victorlenain.fr</span>
        ${nextArrow}
      </div>
    </div>`;
}

function buildHtml(slides) {
  const template = fs.readFileSync(TEMPLATE_PATH, 'utf8');
  const logoUri = fs.existsSync(LOGO_PATH) ? fileUri(LOGO_PATH) : '';
  const html = slides
    .map((text, i) => {
      const { main, sub } = parseSlideText(text);
      return renderSlide({ index: i, total: slides.length, main, sub, logoUri });
    })
    .join('\n');
  return template.replace('{{SLIDES_HTML}}', html);
}

async function renderPdf(html, outputPath) {
  const launchOptions = {
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  }

  const tmpHtml = outputPath.replace(/\.pdf$/, '.tmp.html');
  fs.writeFileSync(tmpHtml, html, 'utf8');

  let browser;
  try {
    browser = await puppeteer.launch(launchOptions);
    const page = await browser.newPage();
    await page.setViewport({ width: SLIDE_WIDTH, height: SLIDE_HEIGHT });
    await page.goto(fileUri(tmpHtml), { waitUntil: 'networkidle0' });
    await page.pdf({
      path: outputPath,
      width: `${SLIDE_WIDTH}px`,
      height: `${SLIDE_HEIGHT}px`,
      printBackground: true,
    });
  } finally {
    if (browser) {
      try { await browser.close(); } catch { /* ignore */ }
    }
    try { fs.unlinkSync(tmpHtml); } catch { /* tmp may not exist */ }
  }
}

async function generateCarouselPdf(slidesInputPath, outputPath) {
  const slidesContent = fs.readFileSync(slidesInputPath, 'utf8');
  const slides = JSON.parse(slidesContent);
  if (!Array.isArray(slides) || slides.length === 0) {
    throw new Error(`Invalid slides input from ${slidesInputPath}`);
  }
  const html = buildHtml(slides);
  await renderPdf(html, outputPath);
  console.log(`PDF generated: ${outputPath} (${slides.length} slides)`);
}

if (require.main === module) {
  const [, , slidesPathArg, outputArg] = process.argv;
  if (!slidesPathArg || !outputArg) {
    console.error('Usage: node html_to_pdf.js <slides.json> <output.pdf>');
    process.exit(1);
  }
  generateCarouselPdf(slidesPathArg, outputArg).catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
