// LinkedIn carousel PDF generator (best practices 2026)
// - Portrait 1080x1350 (mobile-first feed)
// - Logo persistant en haut chaque slide
// - Pattern interrupt automatique au milieu (slide claire) → réduit drop-off de 31%
// - Save cue sur slide finale (boost saves = signal algo fort 2026)

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const SLIDE_WIDTH = 1080;
const SLIDE_HEIGHT = 1350;

const CHROMIUM_EXECUTABLE = process.env.PUPPETEER_EXECUTABLE_PATH || undefined;

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function slideClass(i, total) {
  // Toujours fond dark — pas de pattern interrupt.
  // La différenciation visuelle vient du cover (slide 1) et du CTA (dernière slide).
  if (i === 0) return 'slide slide-cover';
  if (i === total - 1) return 'slide slide-cta';
  return 'slide slide-body';
}

async function generateCarouselPdf(slidesInputPath, outputPath) {
  const slidesContent = fs.readFileSync(slidesInputPath, 'utf8');
  const slides = JSON.parse(slidesContent);
  if (!Array.isArray(slides) || slides.length === 0) {
    throw new Error(`Invalid slides input from ${slidesInputPath}`);
  }
  const total = slides.length;

  const templatePath = path.join(__dirname, 'templates', 'carousel.html');
  const template = fs.readFileSync(templatePath, 'utf8');
  const logoLightPath = path.join(__dirname, 'templates', 'logo-light.png');  // V blanc — fond dark
  const logoDarkPath = path.join(__dirname, 'templates', 'logo.png');         // V noir — fond clair
  const logoLightUri = fs.existsSync(logoLightPath) ? `file://${logoLightPath.replace(/\\/g, '/')}` : '';
  const logoDarkUri = fs.existsSync(logoDarkPath) ? `file://${logoDarkPath.replace(/\\/g, '/')}` : '';

  // Édition badge — affiché sur cover slide. Mode optionnel (env var).
  const editionLabel = (process.env.PIPELINE_MODE || '').toLowerCase() === 'veille'
    ? 'Veille IA'
    : 'Intégration IA';

  const logoUri = logoLightUri;
  const logoImg = logoUri ? `<img src="${logoUri}" alt="Victor Lenain">` : '';

  let slidesHtml = '';
  slides.forEach((text, i) => {
    const lines = text.trim().split('\n').filter(Boolean);
    const main = lines[0] || '';
    const sub = lines.slice(1).join(' ');
    const isFirst = i === 0;
    const isLast = i === total - 1;
    const cls = slideClass(i, total);

    if (isFirst) {
      // Cover slide : design "magazine cover"
      slidesHtml += `
      <div class="${cls}">
        <div class="cover-top">
          <span class="cover-edition">${editionLabel}</span>
          <span class="cover-number">${String(i + 1).padStart(2, '0')}</span>
        </div>
        <div class="cover-center">
          <div class="main-text">${escapeHtml(main)}</div>
          ${sub ? `<div class="sub-text">${escapeHtml(sub)}</div>` : ''}
        </div>
        <div class="cover-signature">
          ${logoImg}
          <div class="sig-text">
            <span class="sig-name">Victor Lenain</span>
            <span class="sig-role">Dev freelance · victorlenain.fr</span>
          </div>
          <span class="sig-swipe">Swipe</span>
        </div>
      </div>`;
      return;
    }

    // Body + CTA slides : header logo + accent-bar + content
    const saveCue = isLast
      ? '<div class="save-cue"><span class="save-cue-badge">ASTUCE</span> Enregistre ce post pour y revenir</div>'
      : '';

    slidesHtml += `
    <div class="${cls}">
      <div class="header">
        ${logoImg}
        <div class="header-text">
          <span class="brand">Victor Lenain</span>
          <span class="brand-sub">Dev freelance · Intégration IA</span>
        </div>
      </div>
      <span class="slide-number">${i + 1} / ${total}</span>
      <div class="body">
        <div class="accent-bar"></div>
        <div class="main-text">${escapeHtml(main)}</div>
        ${sub ? `<div class="sub-text">${escapeHtml(sub)}</div>` : ''}
        ${saveCue}
      </div>
      <div class="footer">
        <span>victorlenain.fr</span>
        ${isLast ? '' : '<span class="swipe">Swipe</span>'}
      </div>
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
    await page.goto(`file://${tmpHtml.replace(/\\/g, '/')}`, { waitUntil: 'networkidle0' });
    await page.pdf({
      path: outputPath,
      width: `${SLIDE_WIDTH}px`,
      height: `${SLIDE_HEIGHT}px`,
      printBackground: true,
    });
    console.log(`PDF generated: ${outputPath} (${total} slides)`);
  } finally {
    if (browser) {
      try { await browser.close(); } catch (e) { /* ignore */ }
    }
    try { fs.unlinkSync(tmpHtml); } catch (e) { /* tmp may not exist */ }
  }
}

const slidesPathArg = process.argv[2];
const outputArg = process.argv[3];
if (!slidesPathArg || !outputArg) {
  console.error('Usage: node html_to_pdf.js <slides.json path> <output.pdf>');
  process.exit(1);
}
generateCarouselPdf(slidesPathArg, outputArg).catch(e => { console.error(e); process.exit(1); });
