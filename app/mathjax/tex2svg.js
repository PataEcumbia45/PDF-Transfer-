// Proceso persistente que convierte fórmulas LaTeX en SVG con MathJax.
// Protocolo: una petición JSON por línea en stdin -> una respuesta JSON por línea en stdout.
//   entrada:  {"id": 1, "tex": "\\frac{a}{b}", "display": true}
//   salida:   {"id": 1, "svg": "<svg ...>", "error": null}
"use strict";

const readline = require("readline");
const { mathjax } = require("mathjax-full/js/mathjax.js");
const { TeX } = require("mathjax-full/js/input/tex.js");
const { SVG } = require("mathjax-full/js/output/svg.js");
const { liteAdaptor } = require("mathjax-full/js/adaptors/liteAdaptor.js");
const { RegisterHTMLHandler } = require("mathjax-full/js/handlers/html.js");
const { AllPackages } = require("mathjax-full/js/input/tex/AllPackages.js");

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

// Funciones con su nombre en español (sen, tg, arcsen…), habituales en los talleres.
const SPANISH = ["sen", "tg", "cotg", "cosec", "arcsen", "arccos", "arctg", "senh", "tgh"];
const macros = Object.fromEntries(SPANISH.map((f) => [f, `\\operatorname{${f}}`]));

const tex = new TeX({
  packages: AllPackages.filter((p) => p !== "bussproofs"),
  macros,
  formatError: (jax, err) => { throw err; },
});
const svg = new SVG({ fontCache: "none" });
const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });

function render(source, display) {
  const node = doc.convert(source, { display, em: 16, ex: 8, containerWidth: 1280 });
  return adaptor.innerHTML(node);
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on("line", (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    return;
  }
  let out;
  try {
    out = { id: req.id, svg: render(String(req.tex || ""), Boolean(req.display)), error: null };
  } catch (err) {
    out = { id: req.id, svg: null, error: String((err && err.message) || err) };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
});
