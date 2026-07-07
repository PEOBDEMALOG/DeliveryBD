/**
 * contingencia-db.js — PEO-BD
 * Camada de armazenamento local via IndexedDB para operação offline/contingência.
 * Expõe funções globais usadas pelo Alpine.js quando modoContingencia === true.
 */

const DB_NAME    = 'peo_bd_contingencia';
const DB_VERSION = 1;

function abrirDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

    req.onupgradeneeded = (e) => {
      const db = e.target.result;

      if (!db.objectStoreNames.contains('remessas_locais')) {
        const storeR = db.createObjectStore('remessas_locais', {
          keyPath: 'id_local',
          autoIncrement: true,
        });
        storeR.createIndex('sincronizado', 'sincronizado', { unique: false });
        storeR.createIndex('criado_em',    'criado_em',    { unique: false });
      }

      if (!db.objectStoreNames.contains('ondas_locais')) {
        const storeO = db.createObjectStore('ondas_locais', {
          keyPath: 'id_local',
          autoIncrement: true,
        });
        storeO.createIndex('sincronizado', 'sincronizado', { unique: false });
        storeO.createIndex('criado_em',    'criado_em',    { unique: false });
      }

      if (!db.objectStoreNames.contains('fila_sincronizacao')) {
        db.createObjectStore('fila_sincronizacao', {
          keyPath: 'id_local',
          autoIncrement: true,
        });
      }
    };

    req.onsuccess = () => resolve(req.result);
    req.onerror   = () => reject(req.error);
  });
}


// ── Remessas ──────────────────────────────────────────────────────────────────

async function salvarRemessaLocal(remessa) {
  const db = await abrirDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction('remessas_locais', 'readwrite');
    const store = tx.objectStore('remessas_locais');
    const req   = store.add({
      ...remessa,
      criado_em:    new Date().toISOString(),
      sincronizado: false,
    });
    req.onsuccess = () => resolve(req.result);
    req.onerror   = () => reject(req.error);
  });
}

async function listarRemessasLocaisNaoSincronizadas() {
  const db = await abrirDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction('remessas_locais', 'readonly');
    const store = tx.objectStore('remessas_locais');
    const req   = store.getAll();
    req.onsuccess = () => resolve(req.result.filter(r => !r.sincronizado));
    req.onerror   = () => reject(req.error);
  });
}


// ── Ondas ─────────────────────────────────────────────────────────────────────

async function salvarOndaLocal(onda) {
  const db = await abrirDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction('ondas_locais', 'readwrite');
    const store = tx.objectStore('ondas_locais');
    const req   = store.add({
      ...onda,
      criado_em:    new Date().toISOString(),
      sincronizado: false,
    });
    req.onsuccess = () => resolve(req.result);
    req.onerror   = () => reject(req.error);
  });
}

async function listarOndasLocaisNaoSincronizadas() {
  const db = await abrirDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction('ondas_locais', 'readonly');
    const store = tx.objectStore('ondas_locais');
    const req   = store.getAll();
    req.onsuccess = () => resolve(req.result.filter(o => !o.sincronizado));
    req.onerror   = () => reject(req.error);
  });
}


// ── Sincronização ─────────────────────────────────────────────────────────────

async function marcarComoSincronizado(id_local, store_name) {
  const db = await abrirDB();
  return new Promise((resolve, reject) => {
    const tx      = db.transaction(store_name, 'readwrite');
    const store   = tx.objectStore(store_name);
    const getReq  = store.get(id_local);
    getReq.onsuccess = () => {
      const item = getReq.result;
      if (!item) { resolve(); return; }
      item.sincronizado = true;
      const putReq = store.put(item);
      putReq.onsuccess = () => resolve();
      putReq.onerror   = () => reject(putReq.error);
    };
    getReq.onerror = () => reject(getReq.error);
  });
}

async function contarPendentes() {
  const [remessas, ondas] = await Promise.all([
    listarRemessasLocaisNaoSincronizadas(),
    listarOndasLocaisNaoSincronizadas(),
  ]);
  return { remessas: remessas.length, ondas: ondas.length, total: remessas.length + ondas.length };
}

async function limparDadosSincronizados() {
  const db = await abrirDB();
  const stores = ['remessas_locais', 'ondas_locais'];
  for (const storeName of stores) {
    await new Promise((resolve, reject) => {
      const tx    = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const req   = store.getAll();
      req.onsuccess = () => {
        req.result.filter(r => r.sincronizado).forEach(r => store.delete(r.id_local));
        tx.oncomplete = resolve;
        tx.onerror    = () => reject(tx.error);
      };
      req.onerror = () => reject(req.error);
    });
  }
}
