const CACHE='fixmyarea-shell-v2';
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(async cache=>{const response=await fetch('/');const html=await response.clone().text();await cache.put('/',response);const assets=[...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map(m=>m[1]);await cache.addAll(['/manifest.webmanifest',...assets])}));self.skipWaiting()});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))));self.clients.claim()});
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(event.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
 if(event.request.mode==='navigate'){event.respondWith(fetch(event.request).catch(()=>caches.match('/')));return}
 if(url.pathname.startsWith('/assets/'))event.respondWith(caches.open(CACHE).then(async cache=>{const cached=await cache.match(event.request);if(cached)return cached;const response=await fetch(event.request);if(response.ok)await cache.put(event.request,response.clone());return response}));
});
