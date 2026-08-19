const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({args: ['--no-sandbox', '--disable-setuid-sandbox']});
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 1700});
  // approbation = page par défaut → servie à la racine (url_path non routable dessus)
  const pages = [['', 'approbation'], ['analytics', 'analytics'], ['learnings', 'learnings'], ['historique', 'historique'], ['tests', 'tests']];
  for (const [path, name] of pages) {
    await page.goto(`http://localhost:8599/${path}`, {waitUntil: 'networkidle2', timeout: 30000});
    await new Promise(r => setTimeout(r, 4500)); // websocket render
    await page.screenshot({path: `/tmp/dash_${name}.png`});
    console.log(`shot ${name}`);
  }
  await browser.close();
})();
