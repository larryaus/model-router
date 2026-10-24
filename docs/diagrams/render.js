// Render every docs/diagrams/*.mmd to a PNG beside it, for the README.
//
// GitHub's mobile app shows a Mermaid block as source code, so the README
// shows these images instead. The diagrams are drawn tall and narrow to stay
// readable at phone width. After editing a .mmd file, run:
//
//   npm install mermaid@11.4.1        (in any directory)
//   node docs/diagrams/render.js <that directory>/node_modules/mermaid/dist/mermaid.min.js
//
// It needs Playwright with Chromium importable from where it runs.

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const MERMAID_CONFIG = {
  startOnLoad: false,
  theme: "neutral",
  fontFamily: "Inter, Helvetica, Arial, sans-serif",
  themeVariables: { fontSize: "16px" },
  flowchart: {
    useMaxWidth: false,
    nodeSpacing: 30,
    rankSpacing: 36,
    padding: 10,
    // Room between a subgraph's title and its first node.
    subGraphTitleMargin: { top: 4, bottom: 12 },
  },
};

async function main() {
  const mermaidJs = process.argv[2];
  if (!mermaidJs || !fs.existsSync(mermaidJs)) {
    console.error("usage: node render.js path/to/mermaid.min.js");
    process.exit(2);
  }
  const files = fs.readdirSync(__dirname).filter((name) => name.endsWith(".mmd")).sort();
  const browser = await chromium.launch();
  const page = await browser.newPage({
    deviceScaleFactor: 2,
    viewport: { width: 2400, height: 1200 },
  });
  await page.setContent(
    '<!doctype html><body style="margin:0;background:#fff"><div id="out"></div></body>'
  );
  await page.addScriptTag({ path: mermaidJs });
  await page.evaluate((config) => window.mermaid.initialize(config), MERMAID_CONFIG);
  for (const [index, name] of files.entries()) {
    const source = fs.readFileSync(path.join(__dirname, name), "utf8");
    const svg = await page.evaluate(
      async ([id, text]) => (await window.mermaid.render(id, text)).svg,
      ["diagram" + index, source]
    );
    await page.evaluate((markup) => {
      document.getElementById("out").innerHTML =
        '<div id="frame" style="display:inline-block;padding:16px;background:#fff">' +
        markup + "</div>";
    }, svg);
    const frame = await page.$("#frame");
    const box = await frame.boundingBox();
    const output = path.join(__dirname, name.replace(/\.mmd$/, ".png"));
    await frame.screenshot({ path: output });
    console.log("%s  %dx%d", path.basename(output), Math.round(box.width), Math.round(box.height));
  }
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
