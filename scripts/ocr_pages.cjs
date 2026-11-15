// Return one TSV result per original page using bundled, local Tesseract data.
const fs = require("node:fs");
const path = require("node:path");
const { once } = require("node:events");

async function main() {
  const [manifestPath, moduleRoot, converterRoot, language] = process.argv.slice(2);
  const pages = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const langPath = path.join(converterRoot, "assets", "ocr", "lang");
  const languages = language.split("+");
  for (const code of languages) {
    fs.accessSync(path.join(langPath, code + ".traineddata.gz"));
  }
  const { createWorker, OEM } = require(path.join(moduleRoot, "tesseract.js"));
  const worker = await createWorker(languages, OEM.LSTM_ONLY, {
    workerPath: path.join(converterRoot, "scripts", "runtime", "ocr-worker.cjs"),
    langPath,
    cacheMethod: "none",
    gzip: true,
    errorHandler: (error) => process.stderr.write(String(error) + "\n"),
  });
  const results = [];
  try {
    for (const page of pages) {
      try {
        const image = fs.readFileSync(page.image);
        const { data } = await worker.recognize(image, {}, { text: true, tsv: true });
        results.push({ number: page.number, tsv: data.tsv ?? "" });
      } catch (error) {
        results.push({ number: page.number, error: String(error) });
      }
    }
  } finally {
    // Tesseract's terminate() signals the worker but does not await its exit.
    const exited = worker.worker.threadId === -1 ? null : once(worker.worker, "exit");
    await worker.terminate();
    await exited;
  }
  process.stdout.write(JSON.stringify(results));
}

main().catch((error) => {
  process.stderr.write(String(error) + "\n");
  process.exitCode = 1;
});
