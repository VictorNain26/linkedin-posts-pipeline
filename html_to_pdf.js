// LinkedIn carousel PDF generator.
// Input  : slides.json — liste d'objets {kind, main, sub, items} (format structuré v2).
//          Rétrocompat : accepte aussi les strings legacy "main\nsub".
// Output : PDF portrait 1080x1350 (best practice 2026)
//
// Kinds de slides :
//   standard — phrase principale (+ mots pivots **accentués** en bleu) + sub
//   list     — titre + items numérotés (badges accent, fini la liste en paragraphe gris)
//   number   — data callout : UN chiffre géant en accent + légende
// La cover se distingue par un titre plus gros (.slide-cover) ; la dernière
// slide par la couleur CTA (.slide-cta). Progress dots dans le footer.

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

// **mot** → <span class="accent">mot</span> (APRÈS escapeHtml : le markup accent
// est généré ici, jamais issu du contenu brut).
function accentify(str) {
  return escapeHtml(str).replace(/\*\*(.+?)\*\*/g, '<span class="accent">$1</span>');
}

// Normalise une slide : objet structuré v2 {kind, main, sub, items} ou string legacy.
function normalizeSlide(raw) {
  if (typeof raw === 'string') {
    const parsed = parseSlideText(raw);
    return { kind: 'standard', main: parsed.main, sub: parsed.sub, items: [] };
  }
  return {
    kind: raw.kind || 'standard',
    main: raw.main || '',
    sub: raw.sub || '',
    items: Array.isArray(raw.items) ? raw.items : [],
  };
}

function progressDots(index, total) {
  const dots = Array.from({ length: total }, (_, i) =>
    `<span class="dot${i === index ? ' active' : ''}"></span>`).join('');
  return `<div class="progress-dots">${dots}</div>`;
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

function renderCover({ index, total, main, sub, logoUri }) {
  // Cover : disposition spécifique — titre en haut, signature en bas, pas de header dupliqué.
  const logoTag = logoUri ? `<img src="${logoUri}" alt="Victor Lenain">` : '';
  const subBlock = sub ? `<div class="sub-text">${accentify(sub)}</div>` : '';
  return `
    <div class="${slideClass(index, total)}">
      <div class="cover-headline">
        <div class="accent-bar"></div>
        <div class="main-text">${accentify(main)}</div>
        ${subBlock}
      </div>
      <div class="cover-bottom">
        <div class="cover-signature">
          ${logoTag}
          <div class="sig-text">
            <span class="sig-name">Victor Lenain</span>
            <span class="sig-role">Dev freelance &middot; Int&eacute;gration IA</span>
          </div>
        </div>
        <span class="cover-arrow">&rarr;</span>
      </div>
    </div>`;
}

function renderStandard({ index, total, kind, main, sub, items, logoUri }) {
  // Body + CTA : disposition uniforme — header logo en haut, content centré, footer en bas.
  const isLast = index === total - 1;
  const logoTag = logoUri ? `<img src="${logoUri}" alt="Victor Lenain">` : '';
  const saveCue = isLast
    ? '<div class="save-cue"><span class="save-cue-badge">ASTUCE</span> Enregistre ce post pour y revenir</div>'
    : '';
  const nextArrow = isLast ? '<span></span>' : '<span class="next-arrow">&rarr;</span>';

  let content;
  if (kind === 'number') {
    // Data callout : le chiffre est le héros, pas de markup accent dedans (déjà bleu)
    const subBlock = sub ? `<div class="sub-text">${accentify(sub)}</div>` : '';
    content = `
        <div class="accent-bar"></div>
        <div class="big-number">${escapeHtml(main)}</div>
        ${subBlock}`;
  } else if (kind === 'list' && items.length > 0) {
    const itemsHtml = items.map((item, n) => `
          <div class="list-item">
            <div class="num">${n + 1}</div>
            <div class="item-text">${accentify(item)}</div>
          </div>`).join('');
    const subBlock = sub ? `<div class="sub-text">${accentify(sub)}</div>` : '';
    content = `
        <div class="accent-bar"></div>
        <div class="main-text">${accentify(main)}</div>
        <div class="list-items">${itemsHtml}</div>
        ${subBlock}`;
  } else {
    const subBlock = sub ? `<div class="sub-text">${accentify(sub)}</div>` : '';
    content = `
        <div class="accent-bar"></div>
        <div class="main-text">${accentify(main)}</div>
        ${subBlock}`;
  }

  return `
    <div class="${slideClass(index, total)} slide-kind-${kind}">
      <div class="header">
        ${logoTag}
        <div class="header-text">
          <span class="brand">Victor Lenain</span>
          <span class="brand-sub">Dev freelance &middot; Int&eacute;gration IA</span>
        </div>
      </div>
      <span class="slide-number">${index + 1} / ${total}</span>
      <div class="body">${content}
        ${saveCue}
      </div>
      <div class="footer">
        <span>victorlenain.fr</span>
        ${progressDots(index, total)}
        ${nextArrow}
      </div>
    </div>`;
}

function renderSlide(params) {
  return params.index === 0 ? renderCover(params) : renderStandard(params);
}

function buildHtml(slides) {
  const template = fs.readFileSync(TEMPLATE_PATH, 'utf8');
  const logoUri = fs.existsSync(LOGO_PATH) ? fileUri(LOGO_PATH) : '';
  const html = slides
    .map((raw, i) => {
      const slide = normalizeSlide(raw);
      return renderSlide({ index: i, total: slides.length, ...slide, logoUri });
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
