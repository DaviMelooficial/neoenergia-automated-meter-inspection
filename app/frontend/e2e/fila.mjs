/**
 * Aceite do M4, ponta a ponta no Chrome real:
 *   login -> 20 decisoes na fila usando SO o teclado -> recarregar /fila sem 404.
 *
 * Pre-requisitos: stack no ar (make up) com itens em fila_humana, e um usuario
 * auditor com senha ja trocada. Nao roda no `make test`: e um roteiro operacional.
 *
 *   E2E_URL=http://localhost:5173 E2E_EMAIL=... E2E_SENHA=... npm run e2e
 */
import { existsSync } from "node:fs";

import puppeteer from "puppeteer-core";

const URL = process.env.E2E_URL ?? "http://localhost:5173";
const EMAIL = process.env.E2E_EMAIL ?? "auditor.e2e@exemplo.com.br";
const SENHA = process.env.E2E_SENHA ?? "senha-e2e-forte-123";
const CANDIDATOS_CHROME = [
  process.env.E2E_CHROME,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);
const CHROME = CANDIDATOS_CHROME.find((c) => existsSync(c));
if (!CHROME) throw new Error("Chrome nao encontrado; defina E2E_CHROME");
const DECISOES = Number(process.env.E2E_DECISOES ?? 20);

const dormir = (ms) => new Promise((r) => setTimeout(r, ms));

async function esperarLeitura(page, diferenteDe) {
  // O badge "leitura #N" muda quando a proxima chega.
  await page.waitForFunction(
    (anterior) => {
      const el = [...document.querySelectorAll("*")].find((e) =>
        /^leitura #\d+$/.test(e.textContent?.trim() ?? ""),
      );
      return el && el.textContent.trim() !== anterior;
    },
    { timeout: 15000 },
    diferenteDe,
  );
  return page.evaluate(
    () =>
      [...document.querySelectorAll("*")]
        .find((e) => /^leitura #\d+$/.test(e.textContent?.trim() ?? ""))
        ?.textContent.trim() ?? "",
  );
}

const navegador = await puppeteer.launch({ executablePath: CHROME, headless: true });
const page = await navegador.newPage();
await page.setViewport({ width: 1400, height: 900 });
const erros = [];
page.on("pageerror", (e) => erros.push(String(e)));
page.on("response", (r) => {
  if (r.status() >= 500) erros.push(`${r.status()} ${r.url()}`);
});

try {
  // 1. login
  await page.goto(`${URL}/login`, { waitUntil: "networkidle0" });
  await page.type('input[type="email"]', EMAIL);
  await page.type('input[type="password"]', SENHA);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => location.pathname === "/fila", { timeout: 15000 });
  let atual = await esperarLeitura(page, "");
  console.log("login ok, primeira", atual);

  // 2. decisoes so por teclado, alternando aprovar / reprovar(motivo) / pular
  const decididas = [];
  const roteiro = ["a", "r2", "a", "r5", "pular"];
  let pulados = 0;
  for (let i = 0; decididas.length < DECISOES; i++) {
    const passo = roteiro[i % roteiro.length];
    const id = Number(atual.replace("leitura #", ""));
    if (passo === "pular") {
      await page.keyboard.press("Space");
      pulados++;
    } else if (passo === "a") {
      await page.keyboard.press("a");
      decididas.push({ id, decisao: "aprovada" });
    } else {
      await page.keyboard.press(passo[1]); // 1-6 marca o motivo
      await dormir(50);
      await page.keyboard.press("r");
      decididas.push({ id, decisao: "reprovada" });
    }
    atual = await esperarLeitura(page, atual);
  }
  console.log(`${decididas.length} decisoes por teclado, ${pulados} puladas`);

  // 3. contador na tela bate com o roteiro
  const contador = await page.evaluate(
    () => document.body.innerText.match(/decididas nesta sessão:\s*(\d+)/)?.[1],
  );
  if (Number(contador) !== DECISOES) throw new Error(`contador na tela = ${contador}`);

  // 4. recarregar /fila direto no servidor: SPA fallback do nginx
  const resposta = await page.goto(`${URL}/fila`, { waitUntil: "networkidle0" });
  if (resposta.status() !== 200) throw new Error(`GET /fila devolveu ${resposta.status()}`);
  await esperarLeitura(page, "");
  console.log("recarregar /fila: 200 e sessao restaurada pelo refresh cookie");

  if (erros.length) throw new Error("erros no navegador: " + erros.join("; "));

  // 5. as decisoes existem no banco: revisoes_humanas e historico_status, via API
  const login = await fetch(`${URL}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: EMAIL, senha: SENHA }),
  });
  if (!login.ok) throw new Error(`login via API falhou: ${login.status}`);
  const cab = { authorization: `Bearer ${(await login.json()).access_token}` };
  for (const d of decididas) {
    const r = await fetch(`${URL}/api/leituras/${d.id}`, { headers: cab });
    const leitura = await r.json();
    const revisao = leitura.revisoes.at(-1);
    const ultimo = leitura.historico.at(-1);
    const esperado = d.decisao === "aprovada" ? "aprovada_humano" : "reprovada_humano";
    if (!revisao || revisao.decisao !== d.decisao) throw new Error(`leitura ${d.id}: revisao ausente`);
    if (ultimo.status !== esperado || !ultimo.ator.startsWith("usuario:")) {
      throw new Error(`leitura ${d.id}: historico ${ultimo.status} por ${ultimo.ator}`);
    }
    if (leitura.status_atual !== esperado) throw new Error(`leitura ${d.id}: status ${leitura.status_atual}`);
  }
  console.log(`${decididas.length} decisoes confirmadas em revisoes_humanas e historico_status`);
  console.log(JSON.stringify({ ok: true, decididas: decididas.length }));
} finally {
  await navegador.close();
}
