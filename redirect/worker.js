// Endereço antigo (de-olho-nas-pecs.deolhonaspecs.workers.dev): manda todo mundo para o domínio novo.
// O "#pec-..." do link é mantido pelo navegador no redirecionamento.
const NOVO = "https://deolhonaspecs.com.br";

// Quem instalou o site como app tem o service worker antigo guardado: este substituto apaga
// as cópias antigas, se desliga e recarrega as abas abertas (que então caem no redirecionamento).
const SW_DESLIGA = `
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) await caches.delete(k);
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) c.navigate(c.url);
})()));
`;

export default {
  fetch(req) {
    const url = new URL(req.url);
    if (url.pathname.endsWith("/sw.js")) {
      return new Response(SW_DESLIGA, { headers: { "content-type": "text/javascript", "cache-control": "no-cache" } });
    }
    return Response.redirect(NOVO + url.pathname + url.search, 301);
  },
};
