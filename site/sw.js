/*
 * Service worker do De Olho nas PECs.
 *
 * Objetivo: abrir rápido, funcionar sem internet e não baixar de novo o que não mudou.
 * - Páginas e dados: tenta a rede primeiro. O navegador manda o pedido com o "ETag" da cópia
 *   que já tem; se nada mudou, o servidor responde "304" sem reenviar o arquivo.
 *   Sem internet, usa a última cópia guardada.
 * - Fontes do Google: nunca mudam, então vêm direto da cópia guardada.
 * - Áudio: não é guardado (arquivo grande, tocado em partes).
 */
const VERSAO = "v1";
const CACHE = "de-olho-" + VERSAO;
const INICIAIS = ["./", "data/lista.json", "manifest.webmanifest", "icones/icone-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(INICIAIS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("de-olho-") && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function redePrimeiro(req) {
  const cache = await caches.open(CACHE);
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (err) {
    const guardada = await cache.match(req, { ignoreSearch: req.mode === "navigate" });
    if (guardada) return guardada;
    if (req.mode === "navigate") { const inicio = await cache.match("./"); if (inicio) return inicio; }
    throw err;
  }
}

async function guardadaPrimeiro(req) {
  const cache = await caches.open(CACHE);
  const guardada = await cache.match(req);
  if (guardada) return guardada;
  const resp = await fetch(req);
  if (resp.ok || resp.type === "opaque") cache.put(req, resp.clone());
  return resp;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") { e.respondWith(guardadaPrimeiro(req)); return; }
  if (url.origin !== self.location.origin) return;            // links para Câmara, Senado etc.: o navegador cuida
  if (url.pathname.startsWith(new URL("audio/", self.registration.scope).pathname)) return;
  e.respondWith(redePrimeiro(req));
});
